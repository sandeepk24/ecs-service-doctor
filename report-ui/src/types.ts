export type Status = "PASS" | "WARN" | "FAIL";

export interface ReportCheck {
  status?: Status;
  message?: string;
  events?: Array<{ created_at?: string; message?: string }>;
  container_images?: Array<{ container: string; image: string }>;
  [key: string]: unknown;
}

export interface ServiceResult {
  cluster: string;
  service: string;
  status: Status;
  critical?: boolean;
  error?: string;
  task_definition?: string;
  launch_type?: string;
  platform_version?: string;
  checks?: Record<string, ReportCheck>;
}

export interface EcsReport {
  tool: string;
  version: string;
  generated_at: string;
  region: string;
  account_check: {
    status: Status;
    message: string;
    actual_account_id?: string;
    expected_account_id?: string;
  };
  summary: {
    total_services: number;
    passed: number;
    warnings: number;
    failed: number;
    critical_failed?: number;
  };
  results: ServiceResult[];
}
