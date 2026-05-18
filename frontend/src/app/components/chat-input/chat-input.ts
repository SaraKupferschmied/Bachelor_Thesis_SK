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

  autoResize(textarea: HTMLTextAreaElement): void {
    textarea.style.height = 'auto';

    const lineHeight = 24;
    const maxLines = 5;
    const maxHeight = lineHeight * maxLines;

    textarea.style.height = Math.min(textarea.scrollHeight, maxHeight) + 'px';
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden';
  }

  handleEnter(event: Event, textarea: HTMLTextAreaElement): void {
    const keyboardEvent = event as KeyboardEvent;

    if (keyboardEvent.ctrlKey) {
      return; // Ctrl + Enter creates a new line
    }

    keyboardEvent.preventDefault();
    this.send();

    setTimeout(() => {
      textarea.style.height = 'auto';
    });
  }
}