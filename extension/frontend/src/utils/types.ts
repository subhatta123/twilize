export interface FieldInfo {
  name: string
  datatype: string
  role: string
  cardinality: number
  sample_values: string[]
}

export interface ShelfAssignment {
  field_name: string
  shelf: string
  aggregation: string
}

export interface ChartPlan {
  chart_type: string
  title: string
  shelves: ShelfAssignment[]
  reason: string
  priority: number
}

export interface DashboardPlan {
  title: string
  layout: string
  charts: ChartPlan[]
}
