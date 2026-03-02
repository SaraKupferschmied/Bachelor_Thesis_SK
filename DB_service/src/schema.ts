import "./environments/environment";

import { DataAccessController } from "./control/data_access_controller";

async function run() {
  
  const statements: string[] = [
    // 1) base tables (no FKs)
    `CREATE TABLE IF NOT EXISTS Faculty (
      faculty_id SERIAL PRIMARY KEY,
      name_de VARCHAR(255),
      name_fr	VARCHAR(255),
      name_en	VARCHAR(255),
      url	VARCHAR(255) NOT NULL UNIQUE,
      faculty_key	VARCHAR(50) 
    );`,

    `INSERT INTO Faculty (faculty_id, name_de, name_fr, name_en, url, faculty_key)
    VALUES (100, 'Fakultät konnte nicht zugeordnet werden', 'Faculté na pas pu être associée', 'Faculty could not be matched', 'about:default-faculty', 'default')
    ON CONFLICT (faculty_id) DO NOTHING;`,

    `CREATE TABLE IF NOT EXISTS Room (
      room_id VARCHAR PRIMARY KEY
    );`,

    `CREATE TABLE IF NOT EXISTS Language (
      lang_id SERIAL PRIMARY KEY,
      description VARCHAR(100) NOT NULL UNIQUE
    );`,

    `CREATE TABLE IF NOT EXISTS Professor (
      prof_id SERIAL PRIMARY KEY,
      title VARCHAR(255),
      first_name VARCHAR(100),
      last_name VARCHAR(100),
      email VARCHAR(255) UNIQUE,
      office VARCHAR(50)
    );`,

    `CREATE UNIQUE INDEX IF NOT EXISTS professor_name_unique
      ON Professor ((COALESCE(first_name, '')), last_name);`,

    // 2) depends on Faculty
    `CREATE TABLE IF NOT EXISTS Domain (
      domain_id SERIAL PRIMARY KEY,
      name VARCHAR(255) NOT NULL,
      faculty_id INT NOT NULL REFERENCES Faculty(faculty_id),
      UNIQUE(name, faculty_id)
    );`,

    // 3) StudyProgram depends on Faculty + Professor
    `CREATE TABLE IF NOT EXISTS StudyProgram (
      program_id SERIAL PRIMARY KEY,
      name VARCHAR(255) NOT NULL,
      degree_level VARCHAR(20) NOT NULL CHECK (degree_level IN ('Bachelor','Master','Doctorate')),
      max_duration_semesters INT,
      total_ects FLOAT,
      min_elective_ects FLOAT,
      max_elective_ects FLOAT,
      study_start VARCHAR(10) CHECK (study_start IN ('Autumn','Spring','Both')),
      faculty_id INT NOT NULL REFERENCES Faculty(faculty_id),
      director INT NULL REFERENCES Professor(prof_id),
      source_hints	jsonb,
      source_faculty_key	VARCHAR(32),	
      source_last_page_url	text,
      name_en TEXT,
      name_de TEXT,
      name_fr TEXT
    );`,

    `CREATE UNIQUE INDEX IF NOT EXISTS uq_studyprogram_natural
      ON studyprogram (name, degree_level, total_ects);`,

    `CREATE INDEX IF NOT EXISTS idx_studyprogram_source_faculty_key
      ON studyprogram (source_faculty_key);`,

    `CREATE INDEX IF NOT EXISTS idx_studyprogram_source_hints_gin
      ON studyprogram
      USING GIN (source_hints);`,

    // 4) Course depends on Faculty + Domain
    `CREATE TABLE IF NOT EXISTS Course (
      code VARCHAR PRIMARY KEY,
      alternative_code VARCHAR,
      name TEXT,
      ects FLOAT,
      description TEXT,
      learning_goals TEXT,
      admission_conditions VARCHAR,
      remarks VARCHAR,
      soft_skills BOOLEAN,
      outside_domain BOOLEAN,
      benefri BOOLEAN,
      mobility BOOLEAN,
      unipop BOOLEAN,
      faculty_id INT NULL REFERENCES Faculty(faculty_id),
      domain_id INT NULL REFERENCES Domain(domain_id)
    );`,

    // 5) relation tables
    `CREATE TABLE IF NOT EXISTS has_lang (
      program_id INT REFERENCES StudyProgram(program_id),
      lang_id INT REFERENCES Language(lang_id),
      PRIMARY KEY (program_id, lang_id)
    );`,

    `CREATE TABLE IF NOT EXISTS teaches (
      code VARCHAR REFERENCES Course(code),
      prof_id INT REFERENCES Professor(prof_id),
      PRIMARY KEY (code, prof_id)
    );`,

    `CREATE TABLE IF NOT EXISTS consist_of (
      program_id INT REFERENCES StudyProgram(program_id),
      code VARCHAR REFERENCES Course(code),
      course_name TEXT,
      course_type VARCHAR(20) CHECK (course_type IN ('Mandatory','Elective')),
      description TEXT,
      PRIMARY KEY (program_id, code)
    );`,

    `CREATE TABLE IF NOT EXISTS Semester (
      sem_id VARCHAR PRIMARY KEY, -- e.g. 'FS-2026'
      year INT NOT NULL,
      type VARCHAR(10) NOT NULL CHECK (type IN ('Autumn','Spring'))
    );`,

    //CourseOffering (term-specific instance)
    `CREATE TABLE IF NOT EXISTS CourseOffering (
      offering_id SERIAL PRIMARY KEY,
      code VARCHAR NOT NULL REFERENCES Course(code),
      sem_id VARCHAR NOT NULL REFERENCES Semester(sem_id),
      offering_type VARCHAR(10) NOT NULL CHECK (offering_type IN ('Weekly','Block')),
      day_time_info TEXT,
      link_course_catalogue VARCHAR NULL,
      UNIQUE (code, sem_id, offering_type)
    );`,

    // Session (term-specific meeting dates for block courses))
    `CREATE TABLE IF NOT EXISTS Session (
      session_id SERIAL PRIMARY KEY,
      offering_id INT NOT NULL REFERENCES CourseOffering(offering_id) ON DELETE CASCADE,
      date DATE NOT NULL,
      start_time TIME NULL,
      end_time TIME NULL,
      room_id VARCHAR NULL REFERENCES Room(room_id),
      unit_type TEXT
    );`,

    // debugging
    `ALTER TABLE Session
      ADD COLUMN IF NOT EXISTS unit_type TEXT;`,

    `CREATE UNIQUE INDEX IF NOT EXISTS uq_session_natural
      ON Session(offering_id, date, start_time, end_time, room_id, unit_type);`,

    `CREATE TABLE IF NOT EXISTS is_taught_in (
      offering_id INT REFERENCES CourseOffering(offering_id) ON DELETE CASCADE,
      lang_id INT REFERENCES Language(lang_id),
      PRIMARY KEY (offering_id, lang_id)
    );`,

    // Evaluation (course_instance-specific)
    `CREATE TABLE IF NOT EXISTS Evaluation (
      eval_id SERIAL PRIMARY KEY,
      offering_id INT NOT NULL REFERENCES CourseOffering(offering_id) ON DELETE CASCADE,
      date DATE NULL,
      start_time TIME NULL,
      end_time TIME NULL,
      description VARCHAR NULL,
      requirements VARCHAR NULL,
      evaluation_scheme VARCHAR(100) NULL,
      remarks VARCHAR NULL
    );`,

    `CREATE TABLE IF NOT EXISTS examined_in (
      room_id VARCHAR REFERENCES Room(room_id),
      eval_id INT REFERENCES Evaluation(eval_id) ON DELETE CASCADE,
      PRIMARY KEY (eval_id, room_id)
    );`,

    `CREATE TABLE IF NOT EXISTS programDocument (
      doc_id SERIAL PRIMARY KEY,
      program_id INT NOT NULL REFERENCES StudyProgram(program_id) ON DELETE CASCADE,
      label TEXT,
      url TEXT NOT NULL,
      doc_type VARCHAR(20) NOT NULL CHECK (doc_type IN ('study_plan','regulation','brochure','other')),
      fetched_at TIMESTAMP NULL,
      parse_status VARCHAR(20) NULL,
      parse_notes TEXT NULL
    );`,

    `CREATE UNIQUE INDEX IF NOT EXISTS uq_programdocument_program_url_type
      ON programDocument (program_id, url, doc_type);`,

    `CREATE TABLE IF NOT EXISTS programCourseStaging (
      staging_id SERIAL PRIMARY KEY,
      program_id INT NOT NULL REFERENCES StudyProgram(program_id) ON DELETE CASCADE,
      raw_text TEXT NOT NULL,
      extracted_code VARCHAR NULL,
      extracted_title TEXT NULL,
      inferred_type VARCHAR(20) NULL CHECK (inferred_type IN ('Mandatory','Elective')),
      source_doc_id INT NULL,
      page_no INT NULL,
      section TEXT NULL,
      created_at TIMESTAMP DEFAULT now()
    );`,

    `CREATE UNIQUE INDEX IF NOT EXISTS uq_programCourseStaging_conflict
      ON programCourseStaging (program_id, extracted_code, source_doc_id, page_no);`

  ];

  for (const sql of statements) {
    await DataAccessController.pool.query(sql);
  }

  console.log("✅ Schema created/updated");
  process.exit(0);
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
