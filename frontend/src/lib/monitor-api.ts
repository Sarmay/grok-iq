import {
  authorizationHeaders,
  isAuthenticationRequiredCode,
  notifyAuthenticationRequired,
} from '@/lib/api'

const API_BASE = String(import.meta.env.VITE_API_BASE_URL ?? '/api').replace(
  /\/$/,
  ''
)

export type Assessment = {
  account_id: number
  monitor_status: string
  risk_score: number
  sample_count: number
  anomaly_count: number
  hard_anomaly_count?: number
  distinct_egress_count?: number
  avg_tps?: number
  max_tps?: number
  latest_tps?: number
  latest_classification?: string
  latest_sample_at?: string | null
  risk_reasons: string[]
  quarantine_until?: string | null
  recovery_guarded?: boolean
  operator_note?: string
  operator_notes?: {
    id: string
    content: string
    created_at: string
    updated_at?: string | null
  }[]
  disposition?: {
    source: string
    sourceLabel: string
    action: string
    actionLabel: string
    reason: string
    at?: string | null
    evidence?: string[]
  } | null
}

export type UpstreamAccount = {
  id: string
  name: string
  email?: string
  provider: string
  enabled: boolean
  authStatus?: string
  priority?: number
  maxConcurrent?: number
  failureCount?: number
  lastUsedAt?: string | null
  createdAt?: string | null
  egressNodeId?: string | null
  egressAssignmentMode?: string
  buildBotFlagged?: boolean
  assessment: Assessment
}

export type EgressNode = {
  id: string
  name: string
  enabled: boolean
  proxyConfigured: boolean
  proxyPool?: boolean
  health?: number
  probeStatus?: string
  exitIp?: string
  assignedAccountCount?: number
}

export type ProbeProfile = {
  id: string
  built_in: boolean
  name: string
  description: string
  model: string
  system_prompt: string
  prompt: string
  expected_text: string
  expected_output: string
  expected_image_url: string
  max_output_tokens: number
  temperature: number | null
  extra_body: Record<string, unknown>
  enabled: boolean
  created_at: string
  updated_at: string
}

export type ProxyTarget = {
  kind: 'current' | 'direct' | 'egress'
  id: number | null
  name?: string
}

export type ProbePlan = {
  id: string
  name: string
  description: string
  profile_id: string
  profile_ids: string[]
  account_scope: 'fixed' | 'all_enabled' | 'risky_enabled' | 'quarantined'
  account_ids: number[]
  proxy_targets: ProxyTarget[]
  execution_mode: 'chat' | 'quality_test'
  rounds: number
  cron_expression: string
  timezone: string
  enabled: boolean
  overlap_policy: 'skip' | 'fill'
  priority: number
  created_at: string
  updated_at: string
  job?: { id: string; name: string; nextRunAt?: string | null } | null
}

export type ProbeRun = {
  id: string
  account_id: number
  account_name: string
  account_email: string
  account_created_at?: string | null
  profile_id: string
  plan_id?: string | null
  status: string
  trigger: string
  rounds: number
  proxy_targets: ProxyTarget[]
  total_steps: number
  completed_steps: number
  error_count: number
  current_round?: number | null
  current_target_key?: string | null
  summary: Record<string, unknown>
  error: string
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  duration_estimate?: ProbeDurationEstimate | null
}

export type ProbeDurationEstimate = {
  average_sample_ms: number
  estimated_total_ms: number
  estimated_remaining_ms: number
  sample_count: number
  updated_at: string
}

export type ProbeSample = {
  id: string
  run_id: string
  account_id: number
  round_number: number
  target_key: string
  target_kind: string
  egress_node_id?: number | null
  egress_name: string
  request_id: string
  status: string
  status_code: number
  output_tokens: number
  reasoning_tokens: number
  first_token_ms: number
  duration_ms: number
  generation_ms: number
  first_token_share: number
  tps: number
  expected_matched?: boolean | null
  response_text: string
  classification: string
  error: string
  created_at: string
}

export type Page<T> = {
  items: T[]
  total: number
  page: number
  pageSize: number
}

type ApiErrorPayload = {
  code?: string
  setupRequired?: boolean
  detail?: string
  error?: { message?: string }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
      ...authorizationHeaders(),
    },
  })
  if (!response.ok) {
    const text = await response.text()
    let payload: ApiErrorPayload | null = null
    try {
      payload = JSON.parse(text) as ApiErrorPayload
    } catch {
      // Non-JSON failures use the raw response body below.
    }
    if (
      response.status === 401 &&
      isAuthenticationRequiredCode(payload?.code)
    ) {
      notifyAuthenticationRequired(Boolean(payload?.setupRequired))
    }
    throw new Error(
      payload?.detail ||
        payload?.error?.message ||
        text ||
        `HTTP ${response.status}`
    )
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function query(
  params: Record<string, string | number | boolean | null | undefined>
) {
  const value = new URLSearchParams()
  for (const [key, item] of Object.entries(params)) {
    if (item !== '' && item != null) value.set(key, String(item))
  }
  const suffix = value.toString()
  return suffix ? `?${suffix}` : ''
}

export const api = {
  health: () => request<Record<string, unknown>>('/health'),
  dashboard: (hours = 168) =>
    request<Record<string, unknown>>(`/dashboard?hours=${hours}`),
  accounts: (params: Record<string, string | number | undefined>) =>
    request<Page<UpstreamAccount>>(`/accounts${query(params)}`),
  account: (id: number) => request<Record<string, unknown>>(`/accounts/${id}`),
  accountAction: (id: number, body: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/accounts/${id}/action`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  deleteAccount: (id: number) =>
    request<Record<string, unknown>>(`/accounts/${id}`, { method: 'DELETE' }),
  egress: (params: Record<string, string | number | undefined> = {}) =>
    request<Page<EgressNode>>(`/egress-nodes${query(params)}`),
  profiles: () => request<ProbeProfile[]>('/probe-profiles'),
  createProfile: (body: Record<string, unknown>) =>
    request<{ id: string }>('/probe-profiles', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateProfile: (id: string, body: Record<string, unknown>) =>
    request<ProbeProfile>(`/probe-profiles/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteProfile: (id: string) =>
    request<void>(`/probe-profiles/${id}`, { method: 'DELETE' }),
  plans: () => request<ProbePlan[]>('/probe-plans'),
  createPlan: (body: Record<string, unknown>) =>
    request<{ id: string }>('/probe-plans', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updatePlan: (id: string, body: Record<string, unknown>) =>
    request<ProbePlan>(`/probe-plans/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deletePlan: (id: string) =>
    request<void>(`/probe-plans/${id}`, { method: 'DELETE' }),
  runPlan: (id: string) =>
    request<Record<string, unknown>>(`/probe-plans/${id}/run`, {
      method: 'POST',
    }),
  createRun: (body: Record<string, unknown>) =>
    request<{ id: string; status: string }>('/probe-runs', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  runs: (params: Record<string, string | number | undefined> = {}) =>
    request<Page<ProbeRun>>(`/probe-runs${query(params)}`),
  run: (id: string) =>
    request<{ run: ProbeRun; profile: ProbeProfile; samples: ProbeSample[] }>(
      `/probe-runs/${id}`
    ),
  cancelRun: (id: string) =>
    request<Record<string, unknown>>(`/probe-runs/${id}/cancel`, {
      method: 'POST',
    }),
  retryRun: (id: string) =>
    request<Record<string, unknown>>(`/probe-runs/${id}/retry`, {
      method: 'POST',
    }),
  deleteRun: (id: string) =>
    request<void>(`/probe-runs/${id}`, { method: 'DELETE' }),
  scheduler: () => request<Record<string, unknown>>('/scheduler'),
  settings: () => request<Record<string, unknown>>('/settings'),
  chatModels: () => request<unknown[]>('/chat/models'),
  chatUrl: `${API_BASE}/responses`,
}
