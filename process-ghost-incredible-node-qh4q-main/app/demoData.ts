/**
 * Hardcoded demo data for instant display when user clicks "Try with Sample Data".
 * Bypasses all agent calls — no live processing needed.
 */

import type { DebugLogEntry } from './sections/DebugPanel'

// The exact input transcript
export const DEMO_TRANSCRIPT_TEXT = `We have a master files here. What we do is we go to monthly actions. Based on what value we have, we fill the actionization, taking the data from transactions. Amazon Web Services, I copy it, go to monthly actions, I paste it here, and the total will get updated automatically.`

export const DEMO_TRANSCRIPT_RESULT = {
  transcript: [
    {
      turn_number: 1,
      speaker: 'Expert',
      timestamp_start: '00:00',
      timestamp_end: '00:04',
      text: 'We have a master files here.',
      language_notes: 'English — refers to a sheet/file without specifying which one',
    },
    {
      turn_number: 2,
      speaker: 'Expert',
      timestamp_start: '00:04',
      timestamp_end: '00:08',
      text: 'What we do is we go to monthly actions.',
      language_notes: 'Mispronounces "Monthly Actuals" as "monthly actions"',
    },
    {
      turn_number: 3,
      speaker: 'Expert',
      timestamp_start: '00:08',
      timestamp_end: '00:14',
      text: 'Based on what value we have, we fill the actionization, taking the data from transactions.',
      language_notes: 'Vague reference — matching logic never explained; "transactions" sheet not named',
    },
    {
      turn_number: 4,
      speaker: 'Expert',
      timestamp_start: '00:14',
      timestamp_end: '00:20',
      text: 'Amazon Web Services, I copy it, go to monthly actions, I paste it here, and the total will get updated automatically.',
      language_notes: 'Uses clean vendor name but actual data is garbled; no column specified; formula dependency not mentioned',
    },
  ],
  total_duration: '00:20',
  speaker_count: 1,
  language_detected: 'English',
}

export const DEMO_EXTRACTION_RESULT = {
  steps: [
    {
      step_number: 1,
      action: 'Open the master files reference sheet',
      evidence_quote: 'We have a master files here.',
      timestamp_start: '00:00',
      timestamp_end: '00:04',
      prerequisites: ['Excel file must be open'],
      confidence: 0.7,
    },
    {
      step_number: 2,
      action: 'Navigate to the Monthly Actuals sheet (speaker says "monthly actions")',
      evidence_quote: 'What we do is we go to monthly actions.',
      timestamp_start: '00:04',
      timestamp_end: '00:08',
      prerequisites: ['Know which sheet tab to click'],
      confidence: 0.6,
    },
    {
      step_number: 3,
      action: 'Identify the value to enter based on matching logic',
      evidence_quote: 'Based on what value we have, we fill the actionization.',
      timestamp_start: '00:08',
      timestamp_end: '00:11',
      prerequisites: ['Understand how rows are matched between sheets'],
      confidence: 0.4,
    },
    {
      step_number: 4,
      action: 'Locate the transaction data from the Transactions sheet',
      evidence_quote: 'taking the data from transactions.',
      timestamp_start: '00:11',
      timestamp_end: '00:14',
      prerequisites: ['Know which sheet contains transaction data'],
      confidence: 0.5,
    },
    {
      step_number: 5,
      action: 'Copy the Amazon Web Services actualized amount',
      evidence_quote: 'Amazon Web Services, I copy it.',
      timestamp_start: '00:14',
      timestamp_end: '00:17',
      prerequisites: ['Identify the correct row in Transactions - March'],
      confidence: 0.5,
    },
    {
      step_number: 6,
      action: 'Paste the value into the Monthly Actuals sheet',
      evidence_quote: 'go to monthly actions, I paste it here.',
      timestamp_start: '00:17',
      timestamp_end: '00:19',
      prerequisites: ['Know which column receives the pasted value'],
      confidence: 0.4,
    },
    {
      step_number: 7,
      action: 'Verify the total updates automatically',
      evidence_quote: 'and the total will get updated automatically.',
      timestamp_start: '00:19',
      timestamp_end: '00:20',
      prerequisites: ['Understand formula dependency'],
      confidence: 0.5,
    },
  ],
  total_steps_extracted: 7,
  coverage_notes: 'Walkthrough is extremely brief (20 seconds). Multiple critical steps are implied but never explicitly stated. The expert demonstrates classic expertise blindness — skipping sheet names, matching logic, column targets, and formula dependencies.',
}

export const DEMO_GAP_DETECTION_RESULT = {
  gaps: [
    {
      gap_id: 1,
      related_step: 1,
      gap_type: 'verbal_gap',
      description: 'Speaker references "master files" without specifying this is a separate sheet called "Master Bills". A new hire would not know which file or sheet is being referred to.',
      bridging_question: 'Which sheet or file is being referred to, and where is it located?',
      timestamp_start: '00:00',
      timestamp_end: '00:04',
      severity: 'high',
    },
    {
      gap_id: 2,
      related_step: 2,
      gap_type: 'verbal_gap',
      description: 'The actual sheet is called "Monthly Actuals" — speaker mispronounces it as "monthly actions". A new hire searching for "monthly actions" would not find it.',
      bridging_question: 'Which sheet exactly should be opened — a new hire would not find "monthly actions" anywhere.',
      timestamp_start: '00:04',
      timestamp_end: '00:08',
      severity: 'high',
    },
    {
      gap_id: 3,
      related_step: 3,
      gap_type: 'verbal_gap',
      description: 'Matching logic never explained. Speaker says "based on what value we have" but never explains how rows in Monthly Actuals correspond to rows in Transactions.',
      bridging_question: 'How do you identify which row in Monthly Actuals corresponds to which transaction — is it matched by Bill ID, vendor name, or transaction string?',
      timestamp_start: '00:08',
      timestamp_end: '00:11',
      severity: 'critical',
    },
    {
      gap_id: 4,
      related_step: 4,
      gap_type: 'verbal_gap',
      description: 'Speaker never says this is a separate sheet called "Transactions - March". Just says "transactions" — a new hire would not know where to find this data.',
      bridging_question: 'Where exactly is the transactions data — which file, which sheet, which column?',
      timestamp_start: '00:11',
      timestamp_end: '00:14',
      severity: 'high',
    },
    {
      gap_id: 5,
      related_step: 5,
      gap_type: 'verbal_gap',
      description: 'The transaction string in the file is "AMZN WEB SVCS IN *AWS53871" not "Amazon Web Services" — speaker is reading a clean name from Master Bills but the actual data in Transactions is garbled bank text. A new hire copying by name would copy the wrong thing.',
      bridging_question: 'The transaction string in the file does not say Amazon Web Services — how do you know which row to copy from?',
      timestamp_start: '00:14',
      timestamp_end: '00:17',
      severity: 'critical',
    },
    {
      gap_id: 6,
      related_step: 6,
      gap_type: 'verbal_gap',
      description: 'No column specified. Speaker says "I paste it here" but never identifies which column in Monthly Actuals receives the value.',
      bridging_question: 'Which column in Monthly Actuals receives the pasted value — the Actual column, column F?',
      timestamp_start: '00:17',
      timestamp_end: '00:19',
      severity: 'high',
    },
    {
      gap_id: 7,
      related_step: 7,
      gap_type: 'verbal_gap',
      description: 'Never mentions that Status must be changed to exactly "Paid" for the SUMIF formula to include the row, and never warns not to overwrite the formula row at the bottom.',
      bridging_question: 'What triggers the total to update — does status need to be changed, and is there a formula row that must not be overwritten?',
      timestamp_start: '00:19',
      timestamp_end: '00:20',
      severity: 'critical',
    },
  ],
  total_gaps_found: 7,
  expertise_blindness_score: '9.2/10 — Extremely high expertise blindness. Nearly every step has an implicit assumption that a new hire cannot replicate without clarification.',
  analysis_notes: 'This 20-second walkthrough contains 7 distinct expertise blindness gaps across a 7-step process. The speaker assumes familiarity with the spreadsheet layout (3 sheets), naming conventions (garbled bank strings vs clean vendor names), column structure, matching logic (Bill ID), and formula dependencies (SUMIF + Paid status). A new hire following this verbatim would fail at nearly every step.',
}

export const DEMO_VISUAL_FILLER_RESULT = {
  visual_fills: [
    {
      gap_id: 1,
      related_step: 1,
      frames_analyzed: 3,
      screen_actions: ['Expert clicks on "Master Bills" tab at bottom of Excel'],
      ui_elements_identified: ['Sheet tab bar showing: Master Bills | Transactions - March | Monthly Actuals'],
      navigation_path: 'Bottom tab bar > Master Bills tab',
      data_entered: null,
      visual_step_description: 'The sheet tab "Master Bills" is visible at the bottom of the Excel window. The expert clicks it, revealing a table with columns: Bill ID, Vendor Name, Category, Monthly Budget.',
      confidence: 0.85,
    },
    {
      gap_id: 3,
      related_step: 3,
      frames_analyzed: 5,
      screen_actions: ['Expert visually scans Bill ID column in Master Bills', 'Switches to Transactions - March sheet', 'Locates matching Bill ID in transactions'],
      ui_elements_identified: ['Bill ID column (Column A) in both sheets', 'Vendor Name column showing clean names in Master Bills'],
      navigation_path: 'Master Bills > Column A (Bill ID) > Transactions - March > Column A (Bill ID)',
      data_entered: null,
      visual_step_description: 'The matching is done by Bill ID (Column A). In Master Bills, Bill ID "BILL-003" corresponds to vendor "Amazon Web Services". In Transactions - March, the same Bill ID "BILL-003" appears next to the garbled string "AMZN WEB SVCS IN *AWS53871".',
      confidence: 0.9,
    },
    {
      gap_id: 5,
      related_step: 5,
      frames_analyzed: 4,
      screen_actions: ['Expert is on Transactions - March sheet', 'Locates row with Bill ID BILL-003', 'Copies the Actualized Amount value ($4,287.50) from Column D'],
      ui_elements_identified: ['Column A: Bill ID', 'Column B: Transaction String (garbled)', 'Column C: Date', 'Column D: Actualized Amount'],
      navigation_path: 'Transactions - March > Row with BILL-003 > Column D (Actualized Amount)',
      data_entered: null,
      visual_step_description: 'The expert copies the Actualized Amount ($4,287.50) from Column D of the Transactions - March sheet, not the transaction string itself. The Bill ID BILL-003 is used to find the right row.',
      confidence: 0.9,
    },
    {
      gap_id: 6,
      related_step: 6,
      frames_analyzed: 3,
      screen_actions: ['Expert switches to Monthly Actuals sheet', 'Navigates to March section', 'Pastes value into Column F (Actual) for the AWS row'],
      ui_elements_identified: ['Column F header: "Actual"', 'March section with vendor rows', 'Row for Amazon Web Services (matched by Bill ID)'],
      navigation_path: 'Monthly Actuals > March section > AWS row > Column F (Actual)',
      data_entered: '$4,287.50',
      visual_step_description: 'The pasted value goes into Column F (Actual) of the Monthly Actuals sheet. The March section is found by scrolling down past January and February blocks.',
      confidence: 0.85,
    },
    {
      gap_id: 7,
      related_step: 7,
      frames_analyzed: 4,
      screen_actions: ['Expert scrolls down past the data rows', 'SUMIF formula row visible at bottom of March section', 'Expert changes Status column (Column E) to "Paid"'],
      ui_elements_identified: ['Column E: Status (with values like "Paid", "Pending")', 'Bottom row: SUMIF formula that sums Column F where Status = "Paid"', 'Formula bar showing: =SUMIF(E:E,"Paid",F:F)'],
      navigation_path: 'Monthly Actuals > March section > Status column > type "Paid"',
      data_entered: 'Paid',
      visual_step_description: 'After pasting the amount, the expert changes the Status column (Column E) to exactly "Paid". The SUMIF formula at the bottom row only sums amounts where Status equals "Paid" (case-sensitive). The formula row must not be overwritten.',
      confidence: 0.92,
    },
  ],
  total_gaps_filled: 5,
  analysis_summary: 'Visual analysis recovered critical context for 5 of the 7 gaps. The screen recordings confirm: (1) the workbook has 3 sheet tabs, (2) matching is by Bill ID, (3) the actual data is garbled bank strings, (4) amounts go in Column F, and (5) Status must be set to "Paid" for the SUMIF formula to include the row. Gaps 2 and 4 are purely verbal (sheet naming) and could not be visually recovered beyond confirming the correct sheet names in the tab bar.',
}

export const DEMO_SOP_RESULT = {
  sop_title: 'How to Enter Monthly Actuals from Bank Transactions into the Bills Tracker',
  sop_sections: [
    {
      section_number: 1,
      section_title: 'Understanding the Workbook Structure',
      steps: [
        {
          step_number: 1,
          instruction: 'Open the Bills Tracker workbook. It contains three sheets: "Master Bills" (reference list with Bill IDs and clean vendor names), "Transactions - March" (raw bank transaction strings with Bill IDs and actualized amounts), and "Monthly Actuals" (where actual amounts are entered month by month).',
          annotation_type: 'verbal_gap',
          evidence_quote: 'We have a master files here.',
          bridging_explanation: 'Gap 1 — Speaker references "master files" without specifying this is a separate sheet. The workbook actually has three distinct sheets that serve different purposes. A new hire needs to understand this structure before beginning.',
          source: 'gap_detection',
          added_in_pass: 1,
        },
      ],
    },
    {
      section_number: 2,
      section_title: 'Navigate to the Correct Sheet',
      steps: [
        {
          step_number: 2,
          instruction: 'Click on the "Monthly Actuals" sheet tab at the bottom of the Excel window. Note: the speaker refers to this as "monthly actions" but the actual sheet name is "Monthly Actuals".',
          annotation_type: 'verbal_gap',
          evidence_quote: 'What we do is we go to monthly actions.',
          bridging_explanation: 'Gap 2 — The actual sheet is called "Monthly Actuals" — speaker mispronounces it. A new hire would not find a sheet called "monthly actions" anywhere in the workbook.',
          source: 'gap_detection',
          added_in_pass: 1,
        },
      ],
    },
    {
      section_number: 3,
      section_title: 'Identify the Row to Update Using Bill ID Matching',
      steps: [
        {
          step_number: 3,
          instruction: 'To determine which row to update, match by Bill ID (Column A). Look up the vendor in "Master Bills" to find the Bill ID (e.g., "BILL-003" for Amazon Web Services), then find the same Bill ID in the "Monthly Actuals" March section.',
          annotation_type: 'verbal_gap',
          evidence_quote: 'Based on what value we have, we fill the actionization.',
          bridging_explanation: 'Gap 3 — Matching logic never explained. The rows are matched by Bill ID across sheets — not by vendor name or by guessing. Without this, a new hire has no way to identify which row corresponds to which transaction.',
          source: 'gap_detection',
          added_in_pass: 1,
        },
      ],
    },
    {
      section_number: 4,
      section_title: 'Locate the Transaction Amount',
      steps: [
        {
          step_number: 4,
          instruction: 'Switch to the "Transactions - March" sheet tab. This sheet contains raw bank transaction strings — the data will appear as garbled text like "AMZN WEB SVCS IN *AWS53871", not clean vendor names.',
          annotation_type: 'verbal_gap',
          evidence_quote: 'taking the data from transactions.',
          bridging_explanation: 'Gap 4 — Speaker never says this is a separate sheet called "Transactions - March". Just saying "transactions" is insufficient — a new hire needs the exact sheet name, file location, and column layout.',
          source: 'gap_detection',
          added_in_pass: 1,
        },
        {
          step_number: 5,
          instruction: 'Find the row with the matching Bill ID (e.g., BILL-003). The transaction string will read "AMZN WEB SVCS IN *AWS53871" — not "Amazon Web Services". Copy the Actualized Amount from Column D (e.g., $4,287.50).',
          annotation_type: 'verbal_gap',
          evidence_quote: 'Amazon Web Services, I copy it.',
          bridging_explanation: 'Gap 5 — The transaction string in the file is "AMZN WEB SVCS IN *AWS53871" not "Amazon Web Services". The speaker reads the clean name from Master Bills but the actual data is garbled bank text. A new hire copying by vendor name would copy the wrong thing. Always match by Bill ID.',
          source: 'gap_detection',
          added_in_pass: 1,
        },
      ],
    },
    {
      section_number: 5,
      section_title: 'Paste the Amount into Monthly Actuals',
      steps: [
        {
          step_number: 6,
          instruction: 'Switch back to the "Monthly Actuals" sheet. Scroll to the March section and find the row for the matching Bill ID. Paste the copied amount into Column F (the "Actual" column) for that row.',
          annotation_type: 'verbal_gap',
          evidence_quote: 'go to monthly actions, I paste it here.',
          bridging_explanation: 'Gap 6 — No column specified. The speaker says "I paste it here" but never identifies Column F (Actual) as the target. Pasting into the wrong column would corrupt the data or overwrite formulas.',
          source: 'gap_detection',
          added_in_pass: 1,
        },
      ],
    },
    {
      section_number: 6,
      section_title: 'Update Status and Verify the Total',
      steps: [
        {
          step_number: 7,
          instruction: 'In the same row, change the Status column (Column E) to exactly "Paid" (case-sensitive). The SUMIF formula at the bottom of the March section only sums amounts where Status equals "Paid". WARNING: Do not overwrite the formula row at the bottom of each month section — new entries must go ABOVE it.',
          annotation_type: 'verbal_gap',
          evidence_quote: 'and the total will get updated automatically.',
          bridging_explanation: 'Gap 7 — Never mentions that Status must be changed to exactly "Paid" for the SUMIF formula to include the row, and never warns not to overwrite the formula row at the bottom. Without setting Status to "Paid", the total will not update. Without the formula warning, a new hire might accidentally destroy the summary calculation.',
          source: 'gap_detection',
          added_in_pass: 1,
        },
        {
          step_number: 8,
          instruction: 'Verify that the total at the bottom of the March section has updated to include the newly entered amount. The Variance column (Actual vs Budget) will also recalculate automatically — do not manually edit it.',
          annotation_type: 'original',
          evidence_quote: 'and the total will get updated automatically.',
          bridging_explanation: null,
          source: 'transcript',
          added_in_pass: 1,
        },
      ],
    },
  ],
  gap_report: [
    {
      gap_id: 1,
      type: 'verbal_gap',
      related_step: 1,
      description: 'Speaker references "master files" without specifying this is a separate sheet called "Master Bills".',
      resolution: 'Added explicit workbook structure explanation identifying all three sheets by name.',
      timestamp: '00:00-00:04',
    },
    {
      gap_id: 2,
      type: 'verbal_gap',
      related_step: 2,
      description: 'The actual sheet is called "Monthly Actuals" — speaker mispronounces it as "monthly actions".',
      resolution: 'Corrected sheet name and added note about the mispronunciation.',
      timestamp: '00:04-00:08',
    },
    {
      gap_id: 3,
      type: 'verbal_gap',
      related_step: 3,
      description: 'Matching logic (Bill ID) never explained — speaker assumes viewer knows how to connect rows across sheets.',
      resolution: 'Added explicit Bill ID matching instruction with example.',
      timestamp: '00:08-00:11',
    },
    {
      gap_id: 4,
      type: 'verbal_gap',
      related_step: 4,
      description: 'Speaker never says this is a separate sheet called "Transactions - March".',
      resolution: 'Named the exact sheet and described the garbled data format.',
      timestamp: '00:11-00:14',
    },
    {
      gap_id: 5,
      type: 'verbal_gap',
      related_step: 5,
      description: 'Transaction string is "AMZN WEB SVCS IN *AWS53871" not "Amazon Web Services" — garbled bank data vs clean name mismatch.',
      resolution: 'Warned about garbled strings and instructed to match by Bill ID instead of vendor name.',
      timestamp: '00:14-00:17',
    },
    {
      gap_id: 6,
      type: 'verbal_gap',
      related_step: 6,
      description: 'No column specified for paste target.',
      resolution: 'Identified Column F (Actual) as the paste target.',
      timestamp: '00:17-00:19',
    },
    {
      gap_id: 7,
      type: 'verbal_gap',
      related_step: 7,
      description: 'Never mentions "Paid" status requirement for SUMIF formula or warns about formula row protection.',
      resolution: 'Added "Paid" status requirement (case-sensitive) and formula row warning.',
      timestamp: '00:19-00:20',
    },
  ],
  metadata: {
    total_original_steps: 7,
    total_gaps_found: 7,
    total_visual_recoveries: 5,
    output_tone: 'step-by-step',
  },
}

export const DEMO_JUDGE_RESULT = {
  verdict: 'APPROVED',
  confidence_score: 0.94,
  evaluation: {
    logical_sequencing: 'Steps follow a logical order: understand structure, navigate to sheet, identify row, locate source data, copy, paste, update status, verify.',
    implicit_assumptions: 'All 7 expertise blindness gaps have been surfaced and annotated with bridging explanations. No remaining implicit assumptions detected.',
    prerequisite_completeness: 'Prerequisites are fully covered — workbook structure, sheet names, column identifiers, matching logic, and formula warnings are all explicit.',
    decision_criteria: 'Bill ID matching is clearly specified. Status value "Paid" (case-sensitive) is called out. Formula row protection is warned against.',
    edge_cases: 'Garbled bank string vs clean vendor name discrepancy is well-documented. SUMIF formula dependency and Variance column auto-calculation are noted.',
  },
  findings: [],
  iteration_notes: 'The SOP comprehensively addresses all 7 detected expertise blindness gaps from the original 20-second walkthrough. Each gap is annotated inline with the relevant bridging explanation and evidence quote. The document transforms an unreproducible verbal walkthrough into a complete, step-by-step procedure that a new hire can follow independently.',
  approval_conditions: 'Approved without conditions. All critical gaps (Bill ID matching, garbled transaction strings, Paid status requirement, SUMIF formula protection) are explicitly addressed.',
}

/**
 * Generate realistic fake debug log entries with sequential timestamps and token counts.
 */
export function generateDemoDebugLogs(): DebugLogEntry[] {
  const baseTime = new Date()
  let offsetMs = 0
  const ts = (addMs: number): string => {
    offsetMs += addMs
    const t = new Date(baseTime.getTime() + offsetMs)
    return t.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  return [
    { timestamp: ts(0), type: 'info', message: 'Pipeline started — Demo Mode (hardcoded output)' },
    { timestamp: ts(50), type: 'info', message: 'Demo mode active — skipping file upload' },

    // Agent 1: Transcription
    { timestamp: ts(200), type: 'agent_start', agentName: 'Transcription Agent', message: 'Running' },
    { timestamp: ts(100), type: 'agent_input', agentName: 'Transcription Agent', message: 'Input payload', payload: 'Transcribe audio: english_hackathon_test.mp4 — Expert walkthrough of Bills Tracker spreadsheet process' },
    { timestamp: ts(2800), type: 'agent_output', agentName: 'Transcription Agent', message: 'Transcript received — 4 turns, 1 speaker, 20s duration', tokenUsage: 1847 },
    { timestamp: ts(100), type: 'agent_complete', agentName: 'Transcription Agent', message: 'Complete' },
    { timestamp: ts(50), type: 'handoff', message: 'HANDOFF: Transcription Agent complete, passing to Process Extraction Agent' },

    // Agent 2: Extraction
    { timestamp: ts(200), type: 'agent_start', agentName: 'Process Extraction Agent', message: 'Running' },
    { timestamp: ts(100), type: 'agent_input', agentName: 'Process Extraction Agent', message: 'Input payload', payload: 'Extract process steps from transcript (4 turns, 20s walkthrough of Bills Tracker entry process)' },
    { timestamp: ts(3400), type: 'agent_output', agentName: 'Process Extraction Agent', message: 'Steps extracted — 7 explicit steps identified', tokenUsage: 2563 },
    { timestamp: ts(100), type: 'agent_complete', agentName: 'Process Extraction Agent', message: 'Complete' },
    { timestamp: ts(50), type: 'handoff', message: 'HANDOFF: Process Extraction Agent complete, passing to Gap Detection Agent' },

    // Agent 3: Gap Detection
    { timestamp: ts(150), type: 'loop', message: 'Starting judge pass 1', iteration: 1 },
    { timestamp: ts(200), type: 'agent_start', agentName: 'Gap Detection Agent', message: 'Running (Pass 1)' },
    { timestamp: ts(100), type: 'agent_input', agentName: 'Gap Detection Agent', message: 'Input payload', payload: 'Detect expertise blindness gaps in 7-step process — 3-sheet Excel workbook (Master Bills, Transactions - March, Monthly Actuals)' },
    { timestamp: ts(4100), type: 'agent_output', agentName: 'Gap Detection Agent', message: '7 gaps detected — expertise blindness score: 9.2/10', tokenUsage: 3891 },
    { timestamp: ts(100), type: 'agent_complete', agentName: 'Gap Detection Agent', message: 'Complete' },
    { timestamp: ts(50), type: 'handoff', message: 'HANDOFF: Gap Detection Agent complete, passing to Visual Gap Filler Agent' },

    // Agent 4: Visual Gap Filler
    { timestamp: ts(200), type: 'agent_start', agentName: 'Visual Gap Filler Agent', message: 'Running' },
    { timestamp: ts(100), type: 'agent_input', agentName: 'Visual Gap Filler Agent', message: 'Input payload', payload: 'Analyze screen recording for visual recovery of 7 gaps — full frame, Bills Tracker spreadsheet' },
    { timestamp: ts(5200), type: 'agent_output', agentName: 'Visual Gap Filler Agent', message: '5 gaps filled via screen analysis — Bill ID matching, column layout, SUMIF formula confirmed', tokenUsage: 4217 },
    { timestamp: ts(100), type: 'agent_complete', agentName: 'Visual Gap Filler Agent', message: 'Complete' },
    { timestamp: ts(50), type: 'handoff', message: 'HANDOFF: Visual Gap Filler Agent complete, passing to Documentation Synthesis Agent' },

    // Agent 5: Synthesis
    { timestamp: ts(200), type: 'agent_start', agentName: 'Documentation Synthesis Agent', message: 'Running' },
    { timestamp: ts(100), type: 'agent_input', agentName: 'Documentation Synthesis Agent', message: 'Input payload', payload: 'Synthesize SOP: "How to Enter Monthly Actuals" — tone: step-by-step, 7 steps + 7 gaps + 5 visual fills' },
    { timestamp: ts(6100), type: 'agent_output', agentName: 'Documentation Synthesis Agent', message: 'SOP generated — 6 sections, 8 steps, 7 annotated gaps', tokenUsage: 5842 },
    { timestamp: ts(100), type: 'agent_complete', agentName: 'Documentation Synthesis Agent', message: 'Complete' },
    { timestamp: ts(50), type: 'handoff', message: 'HANDOFF: Documentation Synthesis Agent complete, passing to SOP Judge Agent' },

    // Agent 6: Judge
    { timestamp: ts(200), type: 'agent_start', agentName: 'SOP Judge Agent', message: 'Running (Pass 1)' },
    { timestamp: ts(100), type: 'agent_input', agentName: 'SOP Judge Agent', message: 'Input payload', payload: 'Audit SOP: 6 sections, 8 steps — verify all 7 gaps addressed, check logical sequencing, validate formula warnings' },
    { timestamp: ts(3800), type: 'agent_output', agentName: 'SOP Judge Agent', message: 'Verdict: APPROVED, Confidence: 94%', tokenUsage: 2934 },
    { timestamp: ts(100), type: 'agent_complete', agentName: 'SOP Judge Agent', message: 'Complete' },

    { timestamp: ts(50), type: 'info', message: 'SOP approved by judge. Pipeline complete — 1 pass, 7 gaps resolved, 0 findings.' },
  ]
}
