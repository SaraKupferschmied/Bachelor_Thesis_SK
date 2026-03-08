import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PlanCardComponent, PlanCardModel } from '../plan-card/plan-card';

@Component({
  selector: 'app-plans-sidebar',
  standalone: true,
  imports: [CommonModule, PlanCardComponent],
  templateUrl: './plans-sidebar.html',
  styleUrl: './plans-sidebar.css'
})
export class PlansSidebarComponent {
  @Input({ required: true }) plans: PlanCardModel[] = [];
  @Input() activePlanId: string | null = null;
}