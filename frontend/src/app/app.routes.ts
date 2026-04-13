import { Routes } from '@angular/router';
import { HomeComponent } from './pages/home/home';
import { StudyPlanPageComponent } from './pages/study-plan/study-plan';

export const routes: Routes = [
  { path: '', component: HomeComponent },
  { path: 'plans/new', component: StudyPlanPageComponent },
  { path: 'plans/:id', component: StudyPlanPageComponent }
];
