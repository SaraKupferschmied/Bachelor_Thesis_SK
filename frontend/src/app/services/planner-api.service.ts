import { Inject, Injectable, PLATFORM_ID, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { isPlatformBrowser } from '@angular/common';
import { Observable } from 'rxjs';
import { LanguageService } from './language.service';

export interface PlannerSemester {
  sem_id: string;
  year: number;
  type: 'Autumn' | 'Spring';
  label: string;
}

export interface PlannerProgram {
  program_id: number;
  degree_level: 'Bachelor' | 'Master' | 'Doctorate' | null;
  total_ects: number | null;
  study_start: 'Autumn' | 'Spring' | 'Both' | null;
  faculty_id: number | null;
  faculty_name: string | null;
  display_name: string | null;
  name_de: string | null;
  name_en: string | null;
  name_fr: string | null;
  languages: string[];
}

export interface PlannerProgramRef {
  program_id: number;
  program_name: string | null;
  course_type?: 'Mandatory' | 'Elective' | null;
}

export interface PlannerCourseOffering {
  offering_id: number;
  sem_id: string;
  offering_type: string | null;
  day_time_info: string | null;
  link_course_catalogue: string | null;
  code: string;
  course_name: string | null;
  ects: number | null;
  teaching_languages: string[];
  mandatory_for: PlannerProgramRef[];
  elective_for: PlannerProgramRef[];
  programs: PlannerProgramRef[];
}

export interface PlannerCoursesResponse {
  semester: {
    sem_id: string;
    year: number;
    type: 'Autumn' | 'Spring';
  } | null;
  selected_programs: Array<{
    program_id: number;
    display_name: string | null;
    degree_level: string | null;
    total_ects: number | null;
  }>;
  courses: PlannerCourseOffering[];
}

export interface PlannerOfferingSession {
  session_id: number;
  offering_id: number;
  date: string;
  weekday: string;
  start_time: string | null;
  end_time: string | null;
  room_id: string | null;
  unit_type: string | null;
}

export interface PlannerProfessor {
  prof_id: number;
  display_name: string;
  email: string | null;
}

export interface PlannerOfferingDetail {
  offering_id: number;
  sem_id: string;
  offering_type: string | null;
  day_time_info: string | null;
  link_course_catalogue: string | null;
  code: string;
  course_name: string | null;
  ects: number | null;
  teaching_languages: string[];
  professors: PlannerProfessor[];
  sessions: PlannerOfferingSession[];
}

@Injectable({ providedIn: 'root' })
export class PlannerApiService {
  private readonly http = inject(HttpClient);
  private readonly languageService = inject(LanguageService);
  private readonly baseUrl: string;

  constructor(@Inject(PLATFORM_ID) platformId: object) {
    this.baseUrl = isPlatformBrowser(platformId)
      ? 'http://localhost:3000'
      : 'http://chatbot:3000';
  }

  getSemesters(): Observable<PlannerSemester[]> {
    return this.http.get<PlannerSemester[]>(`${this.baseUrl}/planner/semesters`);
  }

  getPrograms(locale = this.languageService.currentLanguage()): Observable<PlannerProgram[]> {
    const params = new HttpParams().set('locale', locale);
    return this.http.get<PlannerProgram[]>(`${this.baseUrl}/planner/programs`, { params });
  }

  getCourses(semId: string, programIds: number[], locale = this.languageService.currentLanguage()): Observable<PlannerCoursesResponse> {
    let params = new HttpParams().set('sem_id', semId).set('locale', locale);
    for (const programId of programIds) {
      params = params.append('program_ids', String(programId));
    }
    return this.http.get<PlannerCoursesResponse>(`${this.baseUrl}/planner/courses`, { params });
  }

  getOfferingDetail(offeringId: number): Observable<PlannerOfferingDetail> {
    return this.http.get<PlannerOfferingDetail>(`${this.baseUrl}/planner/offerings/${offeringId}`);
  }
}
