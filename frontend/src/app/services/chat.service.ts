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
  private readonly isBrowser: boolean;

  constructor(
    private http: HttpClient,
    @Inject(PLATFORM_ID) platformId: object
  ) {
    this.isBrowser = isPlatformBrowser(platformId);

    this.baseUrl = this.isBrowser
      ? 'http://localhost:8000'
      : 'http://chatbot:8000';

    console.log('[ChatService] baseUrl =', this.baseUrl);
  }

  private getSessionId(): string {
    if (!this.isBrowser) {
      return 'server-session';
    }

    let sessionId = sessionStorage.getItem('session_id');

    if (!sessionId) {
      sessionId = crypto.randomUUID();
      sessionStorage.setItem('session_id', sessionId);
    }

    return sessionId;
  }

  ask(question: string, language: LanguageCode): Observable<AskResponse> {
    const sessionId = this.getSessionId();

    console.log('[ChatService] sending request', {
      question,
      language,
      session_id: sessionId
    });

    return this.http.post<AskResponse>(`${this.baseUrl}/ask`, {
      question,
      language,
      session_id: sessionId
    });
  }
}
