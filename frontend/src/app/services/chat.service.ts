import { Injectable, Inject, PLATFORM_ID } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap } from 'rxjs';
import { isPlatformBrowser } from '@angular/common';
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
  private readonly baseUrl: string;

  constructor(
    private http: HttpClient,
    @Inject(PLATFORM_ID) platformId: object
  ) {
    this.baseUrl = isPlatformBrowser(platformId)
      ? 'http://localhost:8000'
      : 'http://chatbot:8000';

    console.log('[ChatService] baseUrl =', this.baseUrl);
  }

  ask(question: string, language: LanguageCode): Observable<AskResponse> {
    console.log('[ChatService] sending request', { question, language });

    return this.http.post<AskResponse>(`${this.baseUrl}/ask`, { question, language }).pipe(
      tap({
        next: (res) => console.log('[ChatService] response received', res),
        error: (err) => console.error('[ChatService] response error', err),
      })
    );
  }
}