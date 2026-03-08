import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

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
  @Input({ required: true }) plan!: PlanCardModel;
  @Input() active = false;
}