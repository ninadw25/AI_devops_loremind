export interface Integration {
  id: string;
  name: string;
  logo: string; // Path to logo image
  description: string;
  connected: boolean;
  connecting: boolean;
}

/**
 * Configuration for all available integrations
 * Used for the onboarding connection page
 */
export const INTEGRATIONS: Omit<Integration, 'connected' | 'connecting'>[] = [
  {
    id: 'slack',
    name: 'Slack',
    logo: '/images/slack.png',
    description: 'Team conversations and decisions'
  },
  {
    id: 'gmail',
    name: 'Gmail',
    logo: '/images/gmail.png',
    description: 'Email threads and communications'
  },
  {
    id: 'github',
    name: 'GitHub',
    logo: '/images/github.png',
    description: 'Code, PRs, and technical discussions'
  },
  {
    id: 'jira',
    name: 'Jira',
    logo: '/images/jira.jpg',
    description: 'Project tasks and issue tracking'
  },
  {
    id: 'outlook',
    name: 'Outlook',
    logo: '/images/outlook.png',
    description: 'Enterprise email and calendar'
  }
];

/**
 * Create initial integration state
 */
export const createInitialIntegrations = (): Integration[] => {
  return INTEGRATIONS.map(integration => ({
    ...integration,
    connected: false,
    connecting: false
  }));
};
