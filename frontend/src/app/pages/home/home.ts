import { Component, computed, effect, signal, inject, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { finalize, switchMap } from 'rxjs';
import { PlansSidebarComponent } from '../../components/plans-sidebar/plans-sidebar';
import { OptionCardComponent } from '../../components/option-card/option-card';
import { ChatInputComponent } from '../../components/chat-input/chat-input';
import { Router } from '@angular/router';
import { ChatService, AskResponse, SourceSnippet } from '../../services/chat.service';
import { LanguageCode, LanguageService } from '../../services/language.service';
import { StudyPlanService } from '../../services/study-plan.service';
import { STUDY_PLAN_TRANSLATIONS } from '../../translations';
type ChatMessage = {
  role: 'user' | 'assistant';
  text: string;
  sources?: SourceSnippet[];
  usedTools?: string[];
};

type StudyProgramPlannerForm = {
  studyProgram: string;
  semesters: number | null;
};

type MobilityPlannerForm = {
  semesters: string;
  interest: string;
};

const PLAN_MOBILITY_HERO_MESSAGE = '__hero__:plan_mobility';

const PLAN_STUDY_PROGRAM_HERO_MESSAGE = '__hero__:plan_study_program';

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    PlansSidebarComponent,
    OptionCardComponent,
    ChatInputComponent
  ],
  templateUrl: './home.html',
  styleUrl: './home.css'
})
export class HomeComponent {
  private readonly languageService = inject(LanguageService);
  private readonly studyPlanService = inject(StudyPlanService);
  private readonly router = inject(Router);

  readonly availableLanguages = this.languageService.availableLanguages;
  readonly currentLanguage = this.languageService.currentLanguage;
  readonly dictionary = this.languageService.dictionary;
  readonly plans = this.studyPlanService.plans;


  activePlanId = '1';
  messages: ChatMessage[] = [];
  isloading = false;
  errorMessage = '';
  isSidebarOpen = false;
  isStudyProgramDialogOpen = false;
  studyProgramForm: StudyProgramPlannerForm = {
    studyProgram: '',
    semesters: 6
  };

  constructor(
    private chatService: ChatService,
    private cdr: ChangeDetectorRef
  ) {}

  setLanguage(language: string): void {
    console.log('[Home] setLanguage:', language);
    this.languageService.setLanguage(language as LanguageCode);
  }

  toggleSidebar(): void {
    this.isSidebarOpen = !this.isSidebarOpen;
  }

  closeSidebar(): void {
    this.isSidebarOpen = false;
  }

  onOpenNewPlan(): void {
    this.closeSidebar();
    void this.router.navigate(['/plans/new']);
  }

  onOpenPlan(planId: string): void {
    this.activePlanId = planId;
    this.closeSidebar();
    void this.router.navigate(['/plans', planId]);
  }

  onDeletePlan(planId: string): void {
    this.studyPlanService.deletePlan(planId);
    if (this.activePlanId === planId) {
      this.activePlanId = this.studyPlanService.plans()[0]?.id ?? '';
    }
  }

  onSendMessage(question: string): void {
    console.log('[Home] onSendMessage called with:', question);

    this.errorMessage = '';
    this.isloading = true;
    this.closeSidebar();

    this.messages.push({
      role: 'user',
      text: question
    });

    console.log('[Home] user message pushed', this.messages);

    const lang = this.currentLanguage();
    console.log('[Home] current language:', lang);

    this.chatService.ask(question, lang)
      .pipe(
        finalize(() => {
          console.log('[Home] finalize -> setting isloading=false');
          this.isloading = false;
          this.cdr.detectChanges();
        })
      )
      .subscribe({
        next: (res: AskResponse) => {
          try {
            console.log('[Home] subscribe NEXT fired', res);

            this.messages.push({
              role: 'assistant',
              text: res.answer,
              sources: res.sources,
              usedTools: res.used_tools
            });

            console.log('[Home] assistant message pushed', this.messages);
            this.cdr.detectChanges();
          } catch (e) {
            console.error('[Home] error inside NEXT handler', e);
            throw e;
          }
        },
        error: (err) => {
          console.error('[Home] subscribe ERROR', err);

          this.errorMessage = 'The chatbot request failed.';
          this.messages.push({
            role: 'assistant',
            text: 'Sorry, I could not generate an answer right now.'
          });

          this.cdr.detectChanges();
        },
        complete: () => {
          console.log('[Home] subscribe COMPLETE');
        }
      });
  }


  openStudyProgramDialog(): void {
    this.errorMessage = '';
    this.closeSidebar();
    this.isStudyProgramDialogOpen = true;
  }

  closeStudyProgramDialog(): void {
    if (this.isloading) {
      return;
    }

    this.isStudyProgramDialogOpen = false;
  }

  submitStudyProgramDialog(): void {
    const studyProgram = this.studyProgramForm.studyProgram.trim();
    const semesters = Number(this.studyProgramForm.semesters);

    if (!studyProgram || !Number.isInteger(semesters) || semesters < 1) {
      this.errorMessage = 'Please enter a study program and a valid number of semesters.';
      return;
    }

    const language = this.currentLanguage();
    const visibleUserMessage = this.buildStudyProgramPlannerUserMessage(studyProgram, semesters);
    const detailsMessage = `${visibleUserMessage}\n\nStudy program: ${studyProgram}\nTarget duration: ${semesters} semesters`;

    this.errorMessage = '';
    this.isloading = true;
    this.isStudyProgramDialogOpen = false;
    this.closeSidebar();

    this.messages.push({
      role: 'user',
      text: visibleUserMessage
    });

    this.chatService.ask(PLAN_STUDY_PROGRAM_HERO_MESSAGE, language)
      .pipe(
        switchMap(() => this.chatService.ask(detailsMessage, language)),
        finalize(() => {
          this.isloading = false;
          this.cdr.detectChanges();
        })
      )
      .subscribe({
        next: (res: AskResponse) => {
          this.messages.push({
            role: 'assistant',
            text: res.answer,
            sources: res.sources,
            usedTools: res.used_tools
          });

          this.cdr.detectChanges();
        },
        error: (err) => {
          console.error('[Home] study program planner ERROR', err);

          this.errorMessage = 'The study program planning request failed.';
          this.messages.push({
            role: 'assistant',
            text: 'Sorry, I could not create the study program plan right now.'
          });

          this.cdr.detectChanges();
        }
      });
  }

  private buildStudyProgramPlannerUserMessage(studyProgram: string, semesters: number): string {
    const language = this.currentLanguage();

    if (language === 'de') {
      return `Erstelle bitte einen gesamten Studienplan für ${studyProgram} in ${semesters} Semestern.`;
    }

    if (language === 'fr') {
      return `Crée un plan d’études complet pour ${studyProgram} en ${semesters} semestres.`;
    }

    return `Please create a complete study plan for ${studyProgram} in ${semesters} semesters.`;
  }

  isMobilityDialogOpen = false;

  mobilityForm: MobilityPlannerForm = {
    semesters: '',
    interest: ''
  };

  openMobilityDialog(): void {
    this.errorMessage = '';
    this.closeSidebar();
    this.isMobilityDialogOpen = true;
  }

  closeMobilityDialog(): void {
    if (this.isloading) return;
    this.isMobilityDialogOpen = false;
  }

  submitMobilityDialog(): void {
    const semesters = this.mobilityForm.semesters.trim();
    const interest = this.mobilityForm.interest.trim();

    if (!semesters || !interest) {
      this.errorMessage = 'Please enter your exchange semester(s) and course interest.';
      return;
    }

    const language = this.currentLanguage();

    const visibleUserMessage =
      `Please help me plan my exchange semester(s): ${semesters}. ` +
      `I am interested in courses related to ${interest}.`;

    // Important: keep this clean for the chatbot parser.
    const detailsMessage = `${semesters} ${interest}`;

    this.errorMessage = '';
    this.isloading = true;
    this.isMobilityDialogOpen = false;
    this.closeSidebar();

    this.messages.push({
      role: 'user',
      text: visibleUserMessage
    });

    this.chatService.ask(PLAN_MOBILITY_HERO_MESSAGE, language)
      .pipe(
        switchMap(() => this.chatService.ask(detailsMessage, language)),
        finalize(() => {
          this.isloading = false;
          this.cdr.detectChanges();
        })
      )
      .subscribe({
        next: (res: AskResponse) => {
          this.messages.push({
            role: 'assistant',
            text: res.answer,
            sources: res.sources,
            usedTools: res.used_tools
          });
          this.cdr.detectChanges();
        },
        error: () => {
          this.errorMessage = 'The mobility planning request failed.';
          this.messages.push({
            role: 'assistant',
            text: 'Sorry, I could not create the mobility plan right now.'
          });
          this.cdr.detectChanges();
        }
      });
  }

  onHeroOptionClick(title: string): void {
    const normalizedTitle = title.toLowerCase();

    if (
      normalizedTitle.includes('semesterplan') ||
      normalizedTitle.includes('semester planen') ||
      normalizedTitle.includes('semester plan') ||
      normalizedTitle.includes('create semester plan') ||
      normalizedTitle.includes('upcoming semester') ||
      normalizedTitle.includes('plan semester')
    ) {
      this.onSendMessage('__hero__:plan_semester');
      return;
    }

    if (
      normalizedTitle.includes('gesamter studienplan') ||
      normalizedTitle.includes('complete study plan') ||
      normalizedTitle.includes('plan d’études complet') ||
      normalizedTitle.includes('plan d’études complet')
    ) {
      this.openStudyProgramDialog();
      return;
    }

    if (
      normalizedTitle.includes('auslandsaufenthalt') ||
      normalizedTitle.includes('study abroad') ||
      normalizedTitle.includes('séjour à l’étranger')
    ) {
      this.openMobilityDialog();
      return;
    }

    this.onSendMessage(title);
  }

}
