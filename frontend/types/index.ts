export interface Client {
  client_id: string
  name: string
  advisor_id: string
  document_count?: number
  plan_status?: string
  created_at?: string
}

export interface UploadedDocument {
  document_id: string
  client_id: string
  filename: string
  document_type?: string
  institution?: string
  tax_year?: number
  classification_confidence?: number
  classification_status: string
  created_at?: string
}

export interface FlaggedField {
  field_name: string
  extracted_value: unknown
  confidence?: number
  reason: string
  field_classification: string
}

export interface ContradictionRecord {
  contradiction_id: string
  check_name: string
  severity: string
  description: string
  field_a?: string
  source_a?: string
  value_a?: unknown
  field_b?: string
  source_b?: string
  value_b?: unknown
  suggested_resolution: string
  resolved: boolean
}

export interface GateStatus {
  classification_gate: string
  extraction_gate: string
  validation_gate: string
  snapshot_gate: string
  income_table_gate: string
  overall_status: string
  flagged_fields: FlaggedField[]
  contradictions: ContradictionRecord[]
  missing_fields: string[]
  blocking_reason?: string
}

export interface IrmaaRisk {
  flagged: boolean
  reason: string
  tier_at_risk?: number
}

export interface TaxTrajectoryOutput {
  current_bracket: number
  current_agi: number
  retirement_bracket_estimate: number
  rmd_bracket_estimate: number
  irmaa_risk: IrmaaRisk
  conversion_window_years: number[]
  conversion_window_rationale: string
  years_until_rmd: number
  projected_first_rmd: number
  projected_pretax_at_rmd: number
  urgency: 'high' | 'medium' | 'low'
  ss_taxation_risk: boolean
  narrative: string
  confidence: number
  data_gaps: string[]
}

export interface YearlyConversion {
  year: number
  convert_amount: number
  estimated_federal_tax: number
  estimated_state_tax: number
  bracket_used: string
  post_conversion_agi: number
  irmaa_safe: boolean
  irmaa_note?: string
  aca_safe: boolean
  aca_note?: string
  ss_taxation_impact?: string
  net_benefit_note: string
}

export interface ConversionOptimizerOutput {
  conversion_plan: YearlyConversion[]
  total_converted: number
  estimated_total_tax_on_conversions: number
  liquidity_check_passed: boolean
  liquidity_note?: string
  aca_cliff_risk_years: number[]
  irmaa_cliff_risk_years: number[]
  state_tax_note: string
  narrative: string
  confidence: number
  data_gaps: string[]
}

export interface TLHOpportunity {
  symbol: string
  description: string
  unrealized_loss: number
  holding_period: string
  action: string
  suggested_replacement: string
  wash_sale_risk: 'none' | 'low' | 'high'
  wash_sale_note: string
  estimated_tax_benefit: number
  niit_benefit?: number
}

export interface AssetLocationMove {
  asset_description: string
  current_location: string
  recommended_location: string
  rationale: string
  priority: 'high' | 'medium' | 'low'
}

export interface TLHAdvisorOutput {
  tlh_section_complete: boolean
  tlh_unavailable_reason?: string
  tlh_opportunities: TLHOpportunity[]
  total_harvestable_losses: number
  estimated_total_tax_benefit: number
  asset_location_moves: AssetLocationMove[]
  narrative: string
  confidence: number
  data_gaps: string[]
}

export interface PriorityAction {
  priority: number
  category: 'roth_conversion' | 'tlh' | 'asset_location' | 'other'
  action: string
  rationale: string
  estimated_benefit: string
  urgency: 'immediate' | 'this_year' | 'multi_year'
  confidence: 'high' | 'medium' | 'low'
}

export interface PlanSynthesizerOutput {
  executive_summary: string
  priority_actions: PriorityAction[]
  key_assumptions: string[]
  data_gaps_affecting_plan: string[]
  plan_confidence: number
  disclaimer: string
  narrative: string
}

export interface PlanStepOutputs {
  step_1?: TaxTrajectoryOutput
  step_2?: ConversionOptimizerOutput
  step_3?: TLHAdvisorOutput
  step_4?: PlanSynthesizerOutput
}

export interface Plan {
  plan_id: string
  status: string
  step_outputs: PlanStepOutputs
  created_at?: string
}

export interface Report {
  report_id: string
  plan_id: string
  created_at?: string
  download_url: string
  filename?: string
}

export interface IncomeProjectionRow {
  year: number
  estimated_income: number
  notes?: string
}
