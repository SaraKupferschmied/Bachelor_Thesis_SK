import { Component, EventEmitter, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LanguageService } from '../../services/language.service';

@Component({
  selector: 'app-chat-input',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './chat-input.html',
  styleUrl: './chat-input.css'
})
export class ChatInputComponent {
  private readonly languageService = inject(LanguageService);

  @Output() sendMessage = new EventEmitter<string>();

  message = '';
  readonly dictionary = this.languageService.dictionary;

  send(): void {
    const trimmed = this.message.trim();
    if (!trimmed) return;

    this.sendMessage.emit(trimmed);
    this.message = '';
  }
}