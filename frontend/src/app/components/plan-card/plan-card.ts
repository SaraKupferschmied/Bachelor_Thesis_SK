import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LanguageService } from '../../services/language.service';

export interface PlanCardModel {
  id: string;
  title: string;
  createdAt: string;
  courses: string[];
}

@Component({
  selector: 'app-plan-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './plan-card.html',
  styleUrl: './plan-card.css'
})
export class PlanCardComponent {
  private readonly languageService = inject(LanguageService);

  @Input({ required: true }) plan!: PlanCardModel;
  @Input() active = false;
  @Output() select = new EventEmitter<string>();
  @Output() delete = new EventEmitter<string>();

  readonly dictionary = this.languageService.dictionary;

  onSelect(): void {
    this.select.emit(this.plan.id);
  }

  onDelete(event: Event): void {
    event.stopPropagation();
    this.delete.emit(this.plan.id);
  }
}
