import { Component, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
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

  constructor(private chatService: ChatService) {}

  setLanguage(language: string): void {
    this.languageService.setLanguage(language as LanguageCode);
  }

  onSendMessage(question: string): void {
    this.errorMessage = '';
    this.isloading = true;

    this.messages.push({
      role: 'user',
      text: question
    });

    this.chatService.ask(question, this.currentLanguage()).subscribe({
      next: (res: AskResponse) => {
        this.messages.push({
          role: 'assistant',
          text: res.answer,
          sources: res.sources,
          usedTools: res.used_tools
        });
        this.isloading = false;
      },
      error: (err) => {
        console.error(err);
        this.errorMessage = 'The chatbot request failed.';
        this.messages.push({
          role: 'assistant',
          text: 'Sorry, I could not generate an answer right now.'
        });
        this.isloading = false;
      }
    });
  }
}