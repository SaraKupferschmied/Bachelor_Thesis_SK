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
  chatRoleUser: string;
  chatRoleAssistant: string;
  thinking: string;
  openSource: string;
  toolsUsed: string;
  requestFailed: string;
  answerFailed: string;
  studyProgramDialogTitle: string;
  studyProgramDialogDescription: string;
  studyProgramLabel: string;
  studyProgramPlaceholder: string;
  targetDurationLabel: string;
  cancel: string;
  createStudyPlan: string;
  studyProgramValidationError: string;
  studyProgramPlanningFailed: string;
  studyProgramPlanningFallback: string;
  disclaimer: string;
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
    chatRoleUser: 'Du',
    chatRoleAssistant: 'Chatbot',
    thinking: 'Denke nach...',
    openSource: 'Quelle öffnen',
    toolsUsed: 'Verwendete Tools:',
    requestFailed: 'Die Chatbot-Anfrage ist fehlgeschlagen.',
    answerFailed: 'Entschuldigung, ich konnte gerade keine Antwort generieren.',
    studyProgramDialogTitle: 'Gesamten Studienplan planen',
    studyProgramDialogDescription: 'Nenne mir dein Studienprogramm und die gewünschte Studiendauer und ich erstelle einen ersten Entwurf für dich.',
    studyProgramLabel: 'Studienprogramm',
    studyProgramPlaceholder: 'z.B. Wirtschaftsinformatik',
    targetDurationLabel: 'Zieldauer in Semestern',
    cancel: 'Abbrechen',
    createStudyPlan: 'Studienplan erstellen',
    studyProgramValidationError: 'Bitte gib ein Studienprogramm und eine gültige Anzahl Semester ein.',
    studyProgramPlanningFailed: 'Die Anfrage zur Studienplan-Erstellung ist fehlgeschlagen.',
    studyProgramPlanningFallback: 'Entschuldigung, ich konnte den Studienplan gerade nicht erstellen.',
    disclaimer: 'Hinweis: Dies ist eine Chatbot-Anwendung. Sie kann falsche Ergebnisse erzeugen; überprüfe die Informationen und nutze deinen eigenen Verstand.',
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
        description: 'Plane dein Mobilitätssemester oder -jahr an der UniFr',
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
    chatRoleUser: 'Toi',
    chatRoleAssistant: 'Chatbot',
    thinking: 'Réflexion en cours...',
    openSource: 'Ouvrir la source',
    toolsUsed: 'Outils utilisés :',
    requestFailed: 'La requête au chatbot a échoué.',
    answerFailed: 'Désolé, je n’ai pas pu générer de réponse pour le moment.',
    studyProgramDialogTitle: 'Planifier un programme d’études complet',
    studyProgramDialogDescription: 'Indique ton programme d’études et la durée souhaitée, je vais créer une première proposition pour toi.',
    studyProgramLabel: 'Programme d’études',
    studyProgramPlaceholder: 'p. ex. Informatique de gestion',
    targetDurationLabel: 'Durée cible en semestres',
    cancel: 'Annuler',
    createStudyPlan: 'Créer le plan d’études',
    studyProgramValidationError: 'Indique un programme d’études et un nombre de semestres valide.',
    studyProgramPlanningFailed: 'La demande de planification du programme d’études a échoué.',
    studyProgramPlanningFallback: 'Désolé, je n’ai pas pu créer le plan d’études pour le moment.',
    disclaimer: 'Remarque : ceci est une application de chatbot. Elle peut produire des résultats incorrects ; vérifie les informations et utilise ton propre jugement.',
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
        description: 'Planifie ton semestre ou ton année de mobilité à l’UniFr',
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
    chatRoleUser: 'You',
    chatRoleAssistant: 'Chatbot',
    thinking: 'Thinking...',
    openSource: 'Open source',
    toolsUsed: 'Tools used:',
    requestFailed: 'The chatbot request failed.',
    answerFailed: 'Sorry, I could not generate an answer right now.',
    studyProgramDialogTitle: 'Plan complete study program',
    studyProgramDialogDescription: 'Tell me your study program and target duration and I will create a frist draft for you.',
    studyProgramLabel: 'Study program',
    studyProgramPlaceholder: 'e.g. Business Informatics',
    targetDurationLabel: 'Target duration in semesters',
    cancel: 'Cancel',
    createStudyPlan: 'Create study plan',
    studyProgramValidationError: 'Please enter a study program and a valid number of semesters.',
    studyProgramPlanningFailed: 'The study program planning request failed.',
    studyProgramPlanningFallback: 'Sorry, I could not create the study program plan right now.',
    disclaimer: 'Disclaimer: This is a chatbot application. It may produce incorrect results; check the produced information and think for yourself.',
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
        description: 'Plan your mobility semester or year at UniFr',
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
