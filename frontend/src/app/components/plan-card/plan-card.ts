import { Component, Input, inject } from '@angular/core';
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

  readonly dictionary = this.languageService.dictionary;
}
