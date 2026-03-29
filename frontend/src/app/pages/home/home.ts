import { Component, computed, inject, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { finalize } from 'rxjs';
import { PlansSidebarComponent } from '../../components/plans-sidebar/plans-sidebar';
import { OptionCardComponent } from '../../components/option-card/option-card';
import { ChatInputComponent } from '../../components/chat-input/chat-input';
import { ChatService, AskResponse, SourceSnippet } from '../../services/chat.service';
import { LanguageCode, LanguageService } from '../../services/language.service';

type ChatMessage = {
  role: 'user' | 'assistant';
  text: string;
  sources?: SourceSnippet[];
  usedTools?: string[];
};

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [
    CommonModule,
    PlansSidebarComponent,
    OptionCardComponent,
    ChatInputComponent
  ],
  templateUrl: './home.html',
  styleUrl: './home.css'
})
export class HomeComponent {
  private readonly languageService = inject(LanguageService);

  readonly availableLanguages = this.languageService.availableLanguages;
  readonly currentLanguage = this.languageService.currentLanguage;
  readonly dictionary = this.languageService.dictionary;
  readonly plans = computed(() => this.dictionary().plans);
  readonly options = computed(() => this.dictionary().options);

  activePlanId = '1';
  messages: ChatMessage[] = [];
  isloading = false;
  errorMessage = '';
  isSidebarOpen = false;

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
}
