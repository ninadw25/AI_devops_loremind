/** Tickets API (client-admin) - raise concerns / request subscription changes. */
import { adminFetch } from "./adminHttp";

export type TicketCategory = "service_down" | "subscription_change" | "general";
export type TicketPriority = "low" | "normal" | "high" | "urgent";

export interface Ticket {
  id: string;
  organizationId: string;
  createdBy: string;
  category: string;
  subject: string;
  body: string;
  serviceId: string | null;
  priority: string;
  status: string; // open | in_progress | resolved | closed
  createdAt: string | null;
  updatedAt: string | null;
  resolvedAt: string | null;
}

export interface CreateTicketInput {
  subject: string;
  category?: TicketCategory;
  body?: string;
  serviceId?: string;
  priority?: TicketPriority;
}

export function listTickets(): Promise<Ticket[]> {
  return adminFetch<Ticket[]>("/api/v1/tickets");
}

export function createTicket(input: CreateTicketInput): Promise<Ticket> {
  return adminFetch<Ticket>("/api/v1/tickets", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
