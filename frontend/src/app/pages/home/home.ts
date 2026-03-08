import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PlansSidebarComponent } from '../../components/plans-sidebar/plans-sidebar';
import { OptionCardComponent } from '../../components/option-card/option-card';
import { ChatInputComponent } from '../../components/chat-input/chat-input';
import { PlanCardModel } from '../../components/plan-card/plan-card';

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
  plans: PlanCardModel[] = [
    {
      id: '1',
      title: 'Wintersemester 2025/26',
      createdAt: '15.9.2025',
      courses: [
        'Einführung in die Informatik',
        'Mathematik für Informatiker I',
        'Programmierung I',
        'Diskrete Mathematik'
      ]
    },
    {
      id: '2',
      title: 'Sommersemester 2025',
      createdAt: '20.3.2025',
      courses: [
        'Datenbanken',
        'Mathematik II',
        'Softwareentwicklung',
        'Algorithmen',
        'Web-Technologien'
      ]
    }
  ];

  activePlanId = '1';

  options = [
    {
      icon: '📅',
      title: 'Semesterplan erstellen',
      description: 'Plane deine Kurse für das kommende Semester',
      borderColor: '#e9d5ff'
    },
    {
      icon: '✦',
      title: 'Gesamter Studienplan',
      description: 'Erstelle einen Plan für dein komplettes Studium',
      borderColor: '#bfdbfe'
    },
    {
      icon: '✈',
      title: 'Auslandsaufenthalt',
      description: 'Plane dein Auslandssemester oder -jahr',
      borderColor: '#99f6e4'
    }
  ];
}