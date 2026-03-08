import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-chat-input',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './chat-input.html',
  styleUrl: './chat-input.css'
})
export class ChatInputComponent {
  message = '';

  send(): void {
    console.log('Message:', this.message);
    this.message = '';
  }
}