import { Inject, Injectable, PLATFORM_ID, computed, signal } from '@angular/core';
import { isPlatformBrowser } from '@angular/common';
import {
  PlannerCourseOffering,
  PlannerProgram,
  PlannerProgramRef
} from './planner-api.service';

export type Weekday = 'Montag' | 'Dienstag' | 'Mittwoch' | 'Donnerstag' | 'Freitag';

export interface TimetableSession {
  sessionId?: number;
  date?: string;
  weekday?: string;
  startTime: string;
  endTime: string;
  room?: string | null;
  unitType?: string | null;
}

export interface TimetableEntry {
  courseName: string;
  startTime: string;
  endTime: string;
  location: string;
  color: string;
  offeringId?: number;
  courseCode?: string;
  offeringType?: string | null;
  languages?: string[];
  professors?: string[];
  sessions?: TimetableSession[];
}

export interface StudyPlanProgramSelection {
  id: number;
  name: string;
  degreeLevel?: string | null;
  totalEcts?: number | null;
}

export interface StudyPlanCourseSelection {
  id: number;
  name: string;
  code: string;
  type: 'Pflicht' | 'Wahl';
  ects: number;
  semesterLabel: string;
  languages: string[];
  programs: Array<{
    id: number;
    name: string;
    type: 'Pflicht' | 'Wahl';
  }>;
  offeringType?: string | null;
  dayTimeInfo?: string | null;
  linkCourseCatalogue?: string | null;
  professors?: string[];
  sessions?: Array<{
    sessionId?: number;
    date?: string;
    weekday?: string;
    startTime: string;
    endTime: string;
    room?: string | null;
    unitType?: string | null;
  }>;
}

export interface StudyPlan {
  id: string;
  title: string;
  createdAt: string;
  courses: string[];
  semesterId?: string;
  selectedPrograms?: StudyPlanProgramSelection[];
  selectedOfferings?: StudyPlanCourseSelection[];
  timetable?: Partial<Record<Weekday, TimetableEntry[]>>;
}

@Injectable({ providedIn: 'root' })
export class StudyPlanService {
  private readonly storageKey = 'study-plans';
  private readonly isBrowser: boolean;
  private readonly planState = signal<StudyPlan[]>([]);

  readonly plans = this.planState.asReadonly();
  readonly plansCount = computed(() => this.plans().length);

  constructor(@Inject(PLATFORM_ID) platformId: object) {
    this.isBrowser = isPlatformBrowser(platformId);
    this.planState.set(this.loadPlansFromStorage());
  }

  getPlanById(id: string | null | undefined): StudyPlan | undefined {
    return this.plans().find((plan) => plan.id === id);
  }

  deletePlan(id: string): void {
    this.planState.update((plans) => plans.filter((plan) => plan.id !== id));
    this.persistPlans();
  }

  updatePlanCourses(id: string, courses: string[]): void {
    this.planState.update((plans) =>
      plans.map((plan) => {
        if (plan.id !== id) {
          return plan;
        }

        const filteredOfferings = plan.selectedOfferings?.filter((offering) =>
          courses.includes(offering.name)
        );

        return {
          ...plan,
          courses,
          selectedOfferings: filteredOfferings,
          timetable:
            filteredOfferings && filteredOfferings.length > 0
              ? this.buildTimetableFromOfferings(filteredOfferings)
              : this.buildTimetableFromCourseNames(courses)
        };
      })
    );

    this.persistPlans();
  }

  createPlanFromOfferings(payload: {
    title: string;
    semesterId: string;
    programs: PlannerProgram[];
    offerings: PlannerCourseOffering[];
  }): StudyPlan {
    const selectedOfferings = payload.offerings.map((offering) =>
      this.mapOfferingToSelection(offering, payload.semesterId)
    );

    const createdPlan: StudyPlan = {
      id: this.generateId(),
      title: payload.title,
      createdAt: this.formatDate(new Date()),
      semesterId: payload.semesterId,
      selectedPrograms: payload.programs.map((program) => ({
        id: program.program_id,
        name:
          program.display_name ??
          program.name_de ??
          program.name_en ??
          program.name_fr ??
          `Programm ${program.program_id}`,
        degreeLevel: program.degree_level,
        totalEcts: program.total_ects
      })),
      selectedOfferings,
      courses: selectedOfferings.map((offering) => offering.name),
      timetable: this.buildTimetableFromOfferings(selectedOfferings)
    };

    this.planState.update((plans) => [createdPlan, ...plans]);
    this.persistPlans();
    return createdPlan;
  }

  clearAllPlans(): void {
    this.planState.set([]);
    this.persistPlans();
  }

  private persistPlans(): void {
    if (!this.isBrowser) {
      return;
    }

    try {
      localStorage.setItem(this.storageKey, JSON.stringify(this.planState()));
    } catch {
      // ignore storage errors
    }
  }

  private loadPlansFromStorage(): StudyPlan[] {
    if (!this.isBrowser) {
      return [];
    }

    try {
      const raw = localStorage.getItem(this.storageKey);
      if (!raw) {
        return [];
      }

      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  private generateId(): string {
    if (this.isBrowser && typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
      return crypto.randomUUID();
    }

    return `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
  }

  private mapOfferingToSelection(
    offering: PlannerCourseOffering,
    semesterId: string
  ): StudyPlanCourseSelection {
    const programRefs = this.getUniqueProgramRefs(offering);

    return {
      id: offering.offering_id,
      name: offering.course_name ?? offering.code,
      code: offering.code,
      type: (offering.mandatory_for?.length ?? 0) > 0 ? 'Pflicht' : 'Wahl',
      ects: offering.ects ?? 0,
      semesterLabel: semesterId,
      languages: offering.teaching_languages ?? [],
      offeringType: offering.offering_type,
      dayTimeInfo: offering.day_time_info,
      linkCourseCatalogue: offering.link_course_catalogue,
      programs: programRefs.map((program) => ({
        id: program.program_id,
        name: program.program_name ?? `Programm ${program.program_id}`,
        type: program.course_type === 'Mandatory' ? 'Pflicht' : 'Wahl'
      })),
      professors: [], // to be filled in later when details are fetched
      sessions: []
    };
  }

  private getUniqueProgramRefs(offering: PlannerCourseOffering): PlannerProgramRef[] {
    const refs = [
      ...(offering.programs ?? []),
      ...(offering.mandatory_for ?? []),
      ...(offering.elective_for ?? [])
    ];

    const byId = new Map<number, PlannerProgramRef>();

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

  private buildTimetableFromOfferings(
    offerings: StudyPlanCourseSelection[]
  ): Partial<Record<Weekday, TimetableEntry[]>> {
    const colors = ['#8b5cf6', '#3b82f6', '#14b8a6', '#ec4899', '#f97316', '#22c55e', '#6366f1', '#ef4444'];
    const timetable: Partial<Record<Weekday, TimetableEntry[]>> = {};

    offerings.forEach((offering, index) => {
      const isBlock = (offering.offeringType ?? '').toLowerCase() === 'block';
      const parsed = this.parseDayTimeInfo(offering.dayTimeInfo ?? '');

      if (isBlock) {
        const blockEntry: TimetableEntry = {
          courseName: offering.name,
          startTime: '00:00',
          endTime: '00:00',
          location: 'Blockkurs',
          color: colors[index % colors.length],
          offeringId: offering.id,
          courseCode: offering.code,
          offeringType: offering.offeringType,
          languages: offering.languages,
          professors: offering.professors ?? [],
          sessions: offering.sessions ?? []
        };

        const day: Weekday = 'Montag';
        timetable[day] = [...(timetable[day] ?? []), blockEntry];
        return;
      }

      const day = parsed?.day ?? this.fallbackDay(index);
      const startTime = parsed?.startTime ?? this.fallbackStartTime(index);
      const endTime = parsed?.endTime ?? this.addTwoHours(startTime);
      const location =
        parsed?.location ??
        this.extractLocation(offering.dayTimeInfo) ??
        `Raum ${100 + index}`;

      const entry: TimetableEntry = {
        courseName: offering.name,
        startTime,
        endTime,
        location,
        color: colors[index % colors.length],
        offeringId: offering.id,
        courseCode: offering.code,
        offeringType: offering.offeringType,
        languages: offering.languages,
        professors: offering.professors ?? [],
        sessions: offering.sessions ?? []
      };

      timetable[day] = [...(timetable[day] ?? []), entry].sort((a, b) =>
        a.startTime.localeCompare(b.startTime)
      );
    });

    return timetable;
  }

  private buildTimetableFromCourseNames(
    courses: string[]
  ): Partial<Record<Weekday, TimetableEntry[]>> {
    const colors = ['#8b5cf6', '#3b82f6', '#14b8a6', '#ec4899', '#f97316', '#22c55e'];
    const days: Weekday[] = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag'];
    const timetable: Partial<Record<Weekday, TimetableEntry[]>> = {};

    courses.forEach((course, index) => {
      const day = days[index % days.length];
      const slot = Math.floor(index / days.length);
      const startHour = 8 + slot * 2 + (index % 2 === 0 ? 0 : 1);

      const entry: TimetableEntry = {
        courseName: course,
        startTime: `${String(startHour).padStart(2, '0')}:00`,
        endTime: `${String(startHour + 2).padStart(2, '0')}:00`,
        location: `Raum ${100 + index}`,
        color: colors[index % colors.length]
      };

      timetable[day] = [...(timetable[day] ?? []), entry];
    });

    return timetable;
  }

  private parseDayTimeInfo(
    dayTimeInfo: string
  ): { day: Weekday; startTime: string; endTime: string; location?: string } | null {
    if (!dayTimeInfo) {
      return null;
    }

    const normalized = dayTimeInfo
      .replace(/–|—/g, '-')
      .replace(/\s+/g, ' ')
      .trim();

    const dayMap: Array<{ pattern: RegExp; day: Weekday }> = [
      { pattern: /\b(mon(tag)?|monday)\b/i, day: 'Montag' },
      { pattern: /\b(die(nstag)?|tue(s(day)?)?)\b/i, day: 'Dienstag' },
      { pattern: /\b(mittwoch|wed(nesday)?)\b/i, day: 'Mittwoch' },
      { pattern: /\b(do(nnerstag)?|thu(r(sday)?)?)\b/i, day: 'Donnerstag' },
      { pattern: /\b(frei(tag)?|fri(day)?)\b/i, day: 'Freitag' }
    ];

    const day = dayMap.find((entry) => entry.pattern.test(normalized))?.day;
    const timeMatch = normalized.match(/(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})/);

    if (!day || !timeMatch) {
      return null;
    }

    const [, sh, sm, eh, em] = timeMatch;

    return {
      day,
      startTime: `${sh.padStart(2, '0')}:${sm}`,
      endTime: `${eh.padStart(2, '0')}:${em}`,
      location: this.extractLocation(normalized)
    };
  }

  private extractLocation(dayTimeInfo: string | null | undefined): string | undefined {
    if (!dayTimeInfo) {
      return undefined;
    }

    const roomMatch = dayTimeInfo.match(
      /(?:raum|room|hörsaal|auditorium|seminarraum|pc-pool)\s*[a-z0-9-]*/i
    );

    if (roomMatch) {
      return roomMatch[0].trim();
    }

    const tail = dayTimeInfo
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean);

    return tail.length > 1 ? tail[tail.length - 1] : undefined;
  }

  private fallbackDay(index: number): Weekday {
    const days: Weekday[] = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag'];
    return days[index % days.length];
  }

  private fallbackStartTime(index: number): string {
    const slot = Math.floor(index / 5);
    const startHour = 8 + slot * 2;
    return `${String(startHour).padStart(2, '0')}:00`;
  }

  private addTwoHours(startTime: string): string {
    const [hours, minutes] = startTime.split(':').map(Number);
    return `${String(hours + 2).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
  }

  private formatDate(date: Date): string {
    return `${date.getDate()}.${date.getMonth() + 1}.${date.getFullYear()}`;
  }
}