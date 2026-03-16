import { Injectable, computed, effect, signal } from '@angular/core';

export type LanguageCode = 'de' | 'fr' | 'en';

export interface OptionTranslation {
  icon: string;
  title: string;
  description: string;
  borderColor: string;
}

export interface PlanTranslation {
  id: string;
  title: string;
  createdAt: string;
  courses: string[];
}

interface TranslationDictionary {
  pageTitle: string;
  pageSubtitle: string;
  heroTitle: string;
  heroDescription: string;
  plansTitle: string;
  plansSaved: (count: number) => string;
  newPlan: string;
  messagePlaceholder: string;
  sendLabel: string;
  deletePlan: string;
  moreCourses: (count: number) => string;
  languageLabel: string;
  options: OptionTranslation[];
  plans: PlanTranslation[];
}

const STORAGE_KEY = 'chatbot-language';

const TRANSLATIONS: Record<LanguageCode, TranslationDictionary> = {
  de: {
    pageTitle: 'Semesterplanungs-Assistent',
    pageSubtitle: 'Lass uns dein Semester gemeinsam planen',
    heroTitle: 'Willkommen bei deinem Studienplaner!',
    heroDescription: 'Wähle eine der folgenden Optionen oder stelle mir eine Frage',
    plansTitle: '📖 Meine Pläne',
    plansSaved: (count) => `${count} Pläne gespeichert`,
    newPlan: '＋ Neu',
    messagePlaceholder: 'Schreibe eine Nachricht...',
    sendLabel: 'Senden',
    deletePlan: 'Plan löschen',
    moreCourses: (count) => `+${count} weitere Kurse`,
    languageLabel: 'Sprache',
    options: [
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
    ],
    plans: [
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
    ]
  },
  fr: {
    pageTitle: 'Assistant de planification de semestre',
    pageSubtitle: 'Planifions ensemble ton semestre',
    heroTitle: 'Bienvenue dans ton planificateur d’études !',
    heroDescription: 'Choisis l’une des options suivantes ou pose-moi une question',
    plansTitle: '📖 Mes plans',
    plansSaved: (count) => `${count} plans enregistrés`,
    newPlan: '＋ Nouveau',
    messagePlaceholder: 'Écris un message...',
    sendLabel: 'Envoyer',
    deletePlan: 'Supprimer le plan',
    moreCourses: (count) => `+${count} autres cours`,
    languageLabel: 'Langue',
    options: [
      {
        icon: '📅',
        title: 'Créer un plan de semestre',
        description: 'Planifie tes cours pour le prochain semestre',
        borderColor: '#e9d5ff'
      },
      {
        icon: '✦',
        title: 'Plan d’études complet',
        description: 'Crée un plan pour l’ensemble de tes études',
        borderColor: '#bfdbfe'
      },
      {
        icon: '✈',
        title: 'Séjour à l’étranger',
        description: 'Planifie ton semestre ou ton année à l’étranger',
        borderColor: '#99f6e4'
      }
    ],
    plans: [
      {
        id: '1',
        title: 'Semestre d’hiver 2025/26',
        createdAt: '15.9.2025',
        courses: [
          'Introduction à l’informatique',
          'Mathématiques pour informaticiens I',
          'Programmation I',
          'Mathématiques discrètes'
        ]
      },
      {
        id: '2',
        title: 'Semestre d’été 2025',
        createdAt: '20.3.2025',
        courses: [
          'Bases de données',
          'Mathématiques II',
          'Développement logiciel',
          'Algorithmes',
          'Technologies web'
        ]
      }
    ]
  },
  en: {
    pageTitle: 'Semester Planning Assistant',
    pageSubtitle: 'Let’s plan your semester together',
    heroTitle: 'Welcome to your study planner!',
    heroDescription: 'Choose one of the following options or ask me a question',
    plansTitle: '📖 My plans',
    plansSaved: (count) => `${count} plans saved`,
    newPlan: '＋ New',
    messagePlaceholder: 'Write a message...',
    sendLabel: 'Send',
    deletePlan: 'Delete plan',
    moreCourses: (count) => `+${count} more courses`,
    languageLabel: 'Language',
    options: [
      {
        icon: '📅',
        title: 'Create semester plan',
        description: 'Plan your courses for the upcoming semester',
        borderColor: '#e9d5ff'
      },
      {
        icon: '✦',
        title: 'Complete study plan',
        description: 'Create a plan for your entire degree',
        borderColor: '#bfdbfe'
      },
      {
        icon: '✈',
        title: 'Study abroad',
        description: 'Plan your semester or year abroad',
        borderColor: '#99f6e4'
      }
    ],
    plans: [
      {
        id: '1',
        title: 'Winter Semester 2025/26',
        createdAt: '15.9.2025',
        courses: [
          'Introduction to Computer Science',
          'Mathematics for Computer Scientists I',
          'Programming I',
          'Discrete Mathematics'
        ]
      },
      {
        id: '2',
        title: 'Summer Semester 2025',
        createdAt: '20.3.2025',
        courses: [
          'Databases',
          'Mathematics II',
          'Software Engineering',
          'Algorithms',
          'Web Technologies'
        ]
      }
    ]
  }
};

@Injectable({
  providedIn: 'root'
})
export class LanguageService {
  private readonly initialLanguage = this.resolveInitialLanguage();
  readonly currentLanguage = signal<LanguageCode>(this.initialLanguage);
  readonly dictionary = computed(() => TRANSLATIONS[this.currentLanguage()]);
  constructor() {
    effect(() => {
      const language = this.currentLanguage();

      if (typeof document !== 'undefined') {
        document.documentElement.lang = language;
      }
    });
  }

  readonly availableLanguages: Array<{ code: LanguageCode; label: string }> = [
    { code: 'de', label: 'Deutsch' },
    { code: 'fr', label: 'Français' },
    { code: 'en', label: 'English' }
  ];

  setLanguage(language: LanguageCode): void {
    this.currentLanguage.set(language);

    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, language);
    }
  }

  private resolveInitialLanguage(): LanguageCode {
    if (typeof window === 'undefined') {
      return 'de';
    }

    const storedLanguage = window.localStorage.getItem(STORAGE_KEY);
    if (storedLanguage === 'de' || storedLanguage === 'fr' || storedLanguage === 'en') {
      return storedLanguage;
    }

    const browserLanguage = window.navigator.language.toLowerCase();
    if (browserLanguage.startsWith('fr')) {
      return 'fr';
    }

    if (browserLanguage.startsWith('en')) {
      return 'en';
    }

    return 'de';
  }
}
