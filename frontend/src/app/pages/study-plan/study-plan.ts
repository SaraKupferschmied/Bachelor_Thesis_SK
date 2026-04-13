import { CommonModule } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router, RouterModule } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { filter, finalize } from 'rxjs';
import { PlansSidebarComponent } from '../../components/plans-sidebar/plans-sidebar';
import {
  PlannerApiService,
  PlannerCourseOffering,
  PlannerProgram,
  PlannerSemester,
  PlannerOfferingDetail
} from '../../services/planner-api.service';
import {
  StudyPlanCourseSelection,
  StudyPlanService
} from '../../services/study-plan.service';
import { LanguageService } from '../../services/language.service';

@Component({
  selector: 'app-study-plan-page',
  standalone: true,
  imports: [CommonModule, RouterModule, FormsModule, PlansSidebarComponent],
  templateUrl: './study-plan.html',
  styleUrl: './study-plan.css'
})
export class StudyPlanPageComponent {
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly plannerApi = inject(PlannerApiService);
  private readonly languageService = inject(LanguageService);
  readonly studyPlanService = inject(StudyPlanService);

  readonly weekdays = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag'] as const;
  readonly hourLabels = ['08:00', '09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00', '17:00', '18:00'];

  readonly currentPath = signal(this.router.url);
  readonly currentPlanId = signal<string | null>(this.route.snapshot.paramMap.get('id'));
  readonly isCreateMode = computed(() => this.currentPath().includes('/plans/new'));
  readonly activePlan = computed(() => this.studyPlanService.getPlanById(this.currentPlanId()));

  readonly editMode = signal(false);
  readonly title = signal('');
  readonly selectedSemesterId = signal('');
  readonly selectedProgramIds = signal<number[]>([]);
  readonly selectedOfferingIds = signal<number[]>([]);
  readonly courseSearch = signal('');
  readonly programSearch = signal('');

  readonly semesters = signal<PlannerSemester[]>([]);
  readonly programs = signal<PlannerProgram[]>([]);
  readonly availableCourseOfferings = signal<PlannerCourseOffering[]>([]);

  readonly isLoadingSemesters = signal(false);
  readonly isLoadingPrograms = signal(false);
  readonly isLoadingCourses = signal(false);
  readonly plannerError = signal('');

  readonly selectedCalendarEntry = signal<any | null>(null);
  readonly selectedOfferingDetail = signal<PlannerOfferingDetail | null>(null);
  readonly isLoadingOfferingDetail = signal(false);

  readonly selectedProgramNames = computed(() =>
    this.programs()
      .filter((program) => this.selectedProgramIds().includes(program.program_id))
      .map((program) => this.programLabel(program))
  );

  readonly selectedPlanOfferings = computed<StudyPlanCourseSelection[]>(() =>
    this.activePlan()?.selectedOfferings ?? []
  );

  readonly selectedEditOfferings = computed<StudyPlanCourseSelection[]>(() =>
    this.selectedPlanOfferings().filter((offering) =>
      this.selectedOfferingIds().includes(offering.id)
    )
  );

  readonly filteredPrograms = computed(() => {
    const query = this.programSearch().trim().toLowerCase();
    if (!query) {
      return this.programs();
    }

    return this.programs().filter((program) => {
      const label = this.programLabel(program).toLowerCase();
      return (
        label.includes(query) ||
        (program.degree_level ?? '').toLowerCase().includes(query)
      );
    });
  });

  readonly filteredCourses = computed(() => {
    const query = this.courseSearch().trim().toLowerCase();

    return this.availableCourseOfferings().filter((course) => {
      const programRefs = this.getCourseProgramRefs(course);

      const matchesSearch =
        !query ||
        [
          course.course_name ?? '',
          course.code,
          course.offering_type ?? '',
          course.day_time_info ?? '',
          ...course.teaching_languages,
          ...programRefs.map((program) => program.program_name ?? '')
        ].some((value) => value.toLowerCase().includes(query));

      const matchesPrograms = this.courseMatchesSelectedPrograms(course);

      return matchesSearch && matchesPrograms;
    });
  });

  constructor() {
    this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => {
        this.currentPath.set(this.router.url);
        this.currentPlanId.set(this.route.snapshot.paramMap.get('id'));
        this.syncStateFromRoute();
      });

    this.syncStateFromRoute();
  }

  openPlan(planId: string): void {
    void this.router.navigate(['/plans', planId]);
  }

  createNewPlan(): void {
    void this.router.navigate(['/plans/new']);
  }

  deletePlan(planId: string): void {
    this.studyPlanService.deletePlan(planId);

    if (this.currentPlanId() === planId) {
      void this.router.navigate(['/']);
    }
  }

  goHome(): void {
    void this.router.navigate(['/']);
  }

  startEditing(): void {
    const plan = this.activePlan();
    if (!plan) {
      return;
    }

    this.selectedOfferingIds.set(plan.selectedOfferings?.map((offering) => offering.id) ?? []);
    this.editMode.set(true);
  }

  saveEdit(): void {
    const planId = this.currentPlanId();
    if (!planId) {
      return;
    }

    const plan = this.activePlan();
    if (!plan) {
      return;
    }

    const selectedNames = (plan.selectedOfferings ?? [])
      .filter((offering) => this.selectedOfferingIds().includes(offering.id))
      .map((offering) => offering.name);

    this.studyPlanService.updatePlanCourses(planId, selectedNames);
    this.editMode.set(false);
  }

  removeSelectedOffering(offeringId: number): void {
    this.selectedOfferingIds.set(
      this.selectedOfferingIds().filter((id) => id !== offeringId)
    );
  }

  onSemesterChange(semId: string): void {
    this.selectedSemesterId.set(semId);
    this.selectedOfferingIds.set([]);
    this.availableCourseOfferings.set([]);

    if (semId && this.selectedProgramIds().length > 0) {
      this.loadCourses();
    }
  }

  onProgramsChange(event: Event): void {
    const select = event.target as HTMLSelectElement;

    const ids = Array.from(select.selectedOptions)
      .map((option) => Number(option.value))
      .filter((value) => Number.isInteger(value));

    this.selectedProgramIds.set(ids);
    this.selectedOfferingIds.set([]);
    this.availableCourseOfferings.set([]);

    if (ids.length > 0 && this.selectedSemesterId()) {
      this.loadCourses();
    }
  }

  toggleCourse(offeringId: number): void {
    const selected = this.selectedOfferingIds();
    const exists = selected.includes(offeringId);

    this.selectedOfferingIds.set(
      exists
        ? selected.filter((id) => id !== offeringId)
        : [...selected, offeringId]
    );
  }

  createPlan(): void {
    const semesterId = this.selectedSemesterId();
    const programIds = this.selectedProgramIds();
    const offerings = this.availableCourseOfferings().filter((course) =>
      this.selectedOfferingIds().includes(course.offering_id)
    );

    if (!semesterId || programIds.length === 0 || offerings.length === 0) {
      return;
    }

    const title = this.title().trim() || this.suggestedPlanTitle();
    const selectedPrograms = this.programs().filter((program) =>
      programIds.includes(program.program_id)
    );

    const created = this.studyPlanService.createPlanFromOfferings({
      title,
      semesterId,
      programs: selectedPrograms,
      offerings
    });

    void this.router.navigate(['/plans', created.id]);
  }

  isCourseSelected(offeringId: number): boolean {
    return this.selectedOfferingIds().includes(offeringId);
  }

  isProgramSelected(programId: number): boolean {
    return this.selectedProgramIds().includes(programId);
  }

  programLabel(program: PlannerProgram): string {
    const name =
      program.display_name ??
      program.name_de ??
      program.name_en ??
      program.name_fr ??
      `Programm ${program.program_id}`;

    const ectsPart = program.total_ects ? ` · ${program.total_ects} ECTS` : '';
    const degreePart = program.degree_level ? ` · ${program.degree_level}` : '';

    return `${name}${ectsPart}${degreePart}`;
  }

  offeringTag(course: PlannerCourseOffering): string {
    return (course.mandatory_for?.length ?? 0) > 0 ? 'Pflicht' : 'Wahl';
  }

  offeringPrograms(course: PlannerCourseOffering): string {
    return this.getCourseProgramRefs(course)
      .map((program) => program.program_name)
      .filter(Boolean)
      .join(', ');
  }

  offeringLanguages(course: PlannerCourseOffering): string {
    return course.teaching_languages.join(', ');
  }

    openCalendarEntry(entry: any): void {
    this.selectedCalendarEntry.set(entry);
    this.selectedOfferingDetail.set(null);

    if (!entry.offeringId) {
      return;
    }

    this.isLoadingOfferingDetail.set(true);
    this.plannerApi.getOfferingDetail(entry.offeringId)
      .pipe(finalize(() => this.isLoadingOfferingDetail.set(false)))
      .subscribe({
        next: (detail) => this.selectedOfferingDetail.set(detail),
        error: () => this.selectedOfferingDetail.set(null)
      });
  }

  closeCalendarEntry(): void {
    this.selectedCalendarEntry.set(null);
    this.selectedOfferingDetail.set(null);
  }

  displayCardTitle(entry: any): string {
    return entry.courseName;
  }

  displayCardCode(entry: any): string {
    return entry.courseCode ?? '';
  }

  displayCardTime(entry: any): string {
    return `${entry.startTime} - ${entry.endTime}`;
  }

  plannerCanCreate(): boolean {
    return (
      !!this.selectedSemesterId() &&
      this.selectedProgramIds().length > 0 &&
      this.selectedOfferingIds().length > 0 &&
      !this.isLoadingCourses()
    );
  }

  topPosition(startTime: string): number {
    const [hours, minutes] = startTime.split(':').map(Number);
    return (((hours - 8) * 60) + minutes);
  }

  blockHeight(startTime: string, endTime: string): number {
    const [startHours, startMinutes] = startTime.split(':').map(Number);
    const [endHours, endMinutes] = endTime.split(':').map(Number);
    const duration = (endHours * 60 + endMinutes) - (startHours * 60 + startMinutes);
    return duration;
  }

  private getCourseProgramRefs(course: PlannerCourseOffering) {
    const refs = [
      ...(course.programs ?? []),
      ...(course.mandatory_for ?? []),
      ...(course.elective_for ?? [])
    ];

    const byId = new Map<number, typeof refs[number]>();

    for (const ref of refs) {
      if (!byId.has(ref.program_id)) {
        byId.set(ref.program_id, ref);
        continue;
      }

      const existing = byId.get(ref.program_id)!;
      byId.set(ref.program_id, {
        ...existing,
        ...ref,
        program_name: existing.program_name ?? ref.program_name,
        course_type: existing.course_type ?? ref.course_type
      });
    }

    return Array.from(byId.values());
  }

  private courseMatchesSelectedPrograms(course: PlannerCourseOffering): boolean {
    const selectedProgramIds = new Set(this.selectedProgramIds());

    if (selectedProgramIds.size === 0) {
      return true;
    }

    return this.getCourseProgramRefs(course).some((program) =>
      selectedProgramIds.has(program.program_id)
    );
  }

  private syncStateFromRoute(): void {
    if (this.isCreateMode()) {
      this.editMode.set(false);
      this.title.set('');
      this.programSearch.set('');
      this.courseSearch.set('');
      this.selectedOfferingIds.set([]);
      this.plannerError.set('');

      if (this.semesters().length === 0) {
        this.loadSemesters();
      }

      if (this.programs().length === 0) {
        this.loadPrograms();
      }

      return;
    }

    const plan = this.activePlan();
    if (plan) {
      this.selectedOfferingIds.set(plan.selectedOfferings?.map((offering) => offering.id) ?? []);
      this.editMode.set(false);
    }
  }

  private loadSemesters(): void {
    this.isLoadingSemesters.set(true);
    this.plannerError.set('');

    this.plannerApi.getSemesters()
      .pipe(finalize(() => this.isLoadingSemesters.set(false)))
      .subscribe({
        next: (semesters) => {
          this.semesters.set(semesters);

          if (!this.selectedSemesterId() && semesters.length > 0) {
            this.selectedSemesterId.set(semesters[0].sem_id);
          }
        },
        error: () => {
          this.plannerError.set('Semester konnten nicht geladen werden.');
        }
      });
  }

  private loadPrograms(): void {
    this.isLoadingPrograms.set(true);
    this.plannerError.set('');

    this.plannerApi.getPrograms(this.languageService.currentLanguage())
      .pipe(finalize(() => this.isLoadingPrograms.set(false)))
      .subscribe({
        next: (programs) => {
          this.programs.set(programs);
        },
        error: () => {
          this.plannerError.set('Studienprogramme konnten nicht geladen werden.');
        }
      });
  }

  private loadCourses(): void {
    const semesterId = this.selectedSemesterId();
    const programIds = this.selectedProgramIds();

    if (!semesterId || programIds.length === 0) {
      this.availableCourseOfferings.set([]);
      return;
    }

    this.isLoadingCourses.set(true);
    this.plannerError.set('');

    this.plannerApi.getCourses(
      semesterId,
      programIds,
      this.languageService.currentLanguage()
    )
      .pipe(finalize(() => this.isLoadingCourses.set(false)))
      .subscribe({
        next: (response) => {
          this.availableCourseOfferings.set(response.courses ?? []);
        },
        error: () => {
          this.plannerError.set('Kursangebote konnten nicht geladen werden.');
        }
      });
  }

  private suggestedPlanTitle(): string {
    const semester = this.semesters().find(
      (entry) => entry.sem_id === this.selectedSemesterId()
    );

    return semester ? semester.label : `Semesterplan ${new Date().getFullYear()}`;
  }

    private timeToMinutes(value: string): number {
    const [h, m] = value.split(':').map(Number);
    return h * 60 + m;
  }

  private overlaps(a: { startTime: string; endTime: string }, b: { startTime: string; endTime: string }): boolean {
    const aStart = this.timeToMinutes(a.startTime);
    const aEnd = this.timeToMinutes(a.endTime);
    const bStart = this.timeToMinutes(b.startTime);
    const bEnd = this.timeToMinutes(b.endTime);
    return aStart < bEnd && bStart < aEnd;
  }

  private layoutEntries(entries: any[]): Array<any & { layoutIndex: number; layoutColumns: number }> {
    const sorted = [...entries].sort((a, b) => {
      const diff = this.timeToMinutes(a.startTime) - this.timeToMinutes(b.startTime);
      return diff !== 0 ? diff : this.timeToMinutes(a.endTime) - this.timeToMinutes(b.endTime);
    });

    const result: Array<any & { layoutIndex: number; layoutColumns: number }> = [];
    const groups: Array<Array<any & { layoutIndex: number; layoutColumns: number }>> = [];

    for (const entry of sorted) {
      let placed = false;

      for (const group of groups) {
        if (group.some((existing) => this.overlaps(existing, entry))) {
          const used = new Set(
            group
              .filter((existing) => this.overlaps(existing, entry))
              .map((existing) => existing.layoutIndex)
          );

          let layoutIndex = 0;
          while (used.has(layoutIndex)) {
            layoutIndex++;
          }

          const enriched = { ...entry, layoutIndex, layoutColumns: 1 };
          group.push(enriched);
          placed = true;
          break;
        }
      }

      if (!placed) {
        groups.push([{ ...entry, layoutIndex: 0, layoutColumns: 1 }]);
      }
    }

    for (const group of groups) {
      const columns = Math.max(...group.map((entry) => entry.layoutIndex)) + 1;
      for (const entry of group) {
        entry.layoutColumns = columns;
        result.push(entry);
      }
    }

    return result;
  }

  laidOutEntries(day: typeof this.weekdays[number]): Array<any> {
    const entries = this.activePlan()?.timetable?.[day] ?? [];
    return this.layoutEntries(entries);
  }

  cardWidth(entry: { layoutColumns: number }): string {
    return `calc((100% - 16px) / ${entry.layoutColumns})`;
  }

  cardLeft(entry: { layoutIndex: number; layoutColumns: number }): string {
    return `calc(8px + ${entry.layoutIndex} * ((100% - 16px) / ${entry.layoutColumns}))`;
  }

  professorNames(): string {
    const detail = this.selectedOfferingDetail();
    if (detail?.professors?.length) {
      return detail.professors.map((p) => p.display_name).join(', ');
    }

    const entry = this.selectedCalendarEntry();
    if (entry?.professors?.length) {
      return entry.professors.join(', ');
    }

    return '';
  }

  weeklyEntries(day: typeof this.weekdays[number]): Array<any> {
    const entries = this.activePlan()?.timetable?.[day] ?? [];
    return this.layoutEntries(
      entries.filter((entry) => (entry.offeringType ?? '').toLowerCase() !== 'block')
    );
  }

  blockEntries(): any[] {
    const timetable = this.activePlan()?.timetable;
    if (!timetable) {
      return [];
    }

    const allEntries = this.weekdays.flatMap((day) => timetable[day] ?? []);
    const seen = new Map<string, any>();

    for (const entry of allEntries) {
      if ((entry.offeringType ?? '').toLowerCase() !== 'block') {
        continue;
      }

      const key = String(entry.offeringId ?? `${entry.courseCode}-${entry.courseName}`);
      if (!seen.has(key)) {
        seen.set(key, entry);
      }
    }

    return Array.from(seen.values());
  }

  blockSessionsSummary(entry: any): string {
    const sessions = entry.sessions ?? [];
    if (!sessions.length) {
      return 'Einzelsessions im Detail anzeigen';
    }

    const first = sessions[0];
    const last = sessions[sessions.length - 1];

    if (first?.date && last?.date) {
      return `${sessions.length} Sessions · ${first.date} bis ${last.date}`;
    }

    return `${sessions.length} Sessions`;
  }
}