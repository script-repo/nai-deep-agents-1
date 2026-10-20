// Shapes of the HTTP API (docs/api.md).

export type AuthMode = 'none' | 'token' | 'basic' | 'token+basic';

export interface AppConfig {
  version: string;
  auth: AuthMode;
  allow_model_settings: boolean;
  allow_api_keys: boolean;
  mock: boolean;
}

export interface Agent {
  id: string;
  number: number;
  name: string;
  summary: string;
  icon: string;
  example_prompts: string[];
  tools: string[];
  subagents: { name: string; description: string }[];
  interrupt_on: string[];
}

export interface CatalogModel {
  name: string;
  provider: string;
  model: string;
  input_per_mtok: number | null;
  output_per_mtok: number | null;
  context_window: number | null;
  tags: string[];
}

export interface ModelSettings {
  provider: string;
  model: string;
  base_url: string | null;
  temperature: number;
  has_api_key: boolean;
}

export interface SettingsInfo {
  model: ModelSettings;
  providers: string[];
  editable: boolean;
  api_keys_allowed: boolean;
}

export type ThreadStatus = 'idle' | 'running' | 'interrupted' | 'error';

export interface Thread {
  id: string;
  agent_id: string;
  owner: string;
  title: string;
  created_at: string;
  updated_at: string;
  status: ThreadStatus;
}

export type TodoStatus = 'pending' | 'in_progress' | 'completed';
export interface Todo {
  content: string;
  status: TodoStatus;
}

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export type ApiMessage =
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string; tool_calls?: ToolCall[] }
  | { role: 'tool'; name: string; tool_call_id: string; content: string }
  | { role: string; content: string };

export interface ActionRequest {
  name: string;
  args: Record<string, unknown>;
  description?: string;
}

export interface ReviewConfig {
  action_name: string;
  allowed_decisions: string[];
}

export interface Interrupt {
  id: string | null;
  actions: ActionRequest[];
  review_configs: ReviewConfig[];
}

export interface ThreadSnapshot {
  thread: Thread;
  messages: ApiMessage[];
  todos: Todo[];
  files: Record<string, string>;
  interrupts: Interrupt[];
}

export type Decision =
  | { type: 'approve' }
  | { type: 'reject'; message?: string }
  | { type: 'edit'; edited_action: { name: string; args: Record<string, unknown> } };

// ---- run stream events ----

export type RunEvent =
  | { type: 'token'; text: string; agent: string }
  | { type: 'tool_call'; id: string; name: string; args: Record<string, unknown>; agent: string }
  | { type: 'tool_result'; id: string; name: string; content: string; agent: string; status?: string }
  | { type: 'todos'; todos: Todo[] }
  | { type: 'files'; files: Record<string, string>; agent?: string }
  | { type: 'subagent'; status: 'start' | 'end'; id: string; name: string; description: string; result?: string }
  | ({ type: 'interrupt' } & Interrupt)
  | { type: 'done'; final: string; interrupted: boolean }
  | { type: 'error'; message: string };
