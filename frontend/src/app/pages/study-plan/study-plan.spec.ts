import { ComponentFixture, TestBed } from '@angular/core/testing';

import { StudyPlanPageComponent } from './study-plan';

describe('StudyPlan', () => {
  let component: StudyPlanPageComponent;
  let fixture: ComponentFixture<StudyPlanPageComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [StudyPlanPageComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(StudyPlanPageComponent);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
