import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-option-card',
  standalone: true,
  templateUrl: './option-card.html',
  styleUrl: './option-card.css'
})
export class OptionCardComponent {
  @Input({ required: true }) icon!: string;
  @Input({ required: true }) title!: string;
  @Input({ required: true }) description!: string;
  @Input() borderColor = '#e9d5ff';
}