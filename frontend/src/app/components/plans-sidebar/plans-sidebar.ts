import { Component, Input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PlanCardComponent, PlanCardModel } from '../plan-card/plan-card';
import { LanguageService } from '../../services/language.service';

@Component({
  selector: 'app-plans-sidebar',
  standalone: true,
  imports: [CommonModule, PlanCardComponent],
  templateUrl: './plans-sidebar.html',
  styleUrl: './plans-sidebar.css'
})
export class PlansSidebarComponent {
  private readonly languageService = inject(LanguageService);

  @Input({ required: true }) plans: PlanCardModel[] = [];
  @Input() activePlanId: string | null = null;

  readonly dictionary = this.languageService.dictionary;

  get plansSavedText(): string {
    return this.dictionary().plansSaved(this.plans.length);
  }
}
