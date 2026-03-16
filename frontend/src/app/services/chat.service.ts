import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { LanguageCode } from './language.service';

export interface SourceSnippet {
  source: string;
  snippet: string;
  page?: number;
  source_type: 'pdf' | 'api';
  metadata?: Record<string, any>;
}

export interface AskResponse {
  answer: string;
  sources: SourceSnippet[];
  used_tools: string[];
}

@Injectable({
  providedIn: 'root'
})
export class ChatService {
  private baseUrl = 'http://localhost:8000';

  constructor(private http: HttpClient) {}

  ask(question: string, language: LanguageCode): Observable<AskResponse> {
    return this.http.post<AskResponse>(`${this.baseUrl}/ask`, { question, language });
  }
}
