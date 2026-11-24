import { useState, useEffect } from 'react';
import {
  getDraft,
  saveDraft as apiSaveDraft,
  clearDraft as apiClearDraft,
  DEFAULT_CREDENTIALS,
  type IntegrationCredentials,
} from '@/services/integrationSetupApi';

export function useIntegrationSetup() {
  const [setupData, setSetupData] = useState<Required<IntegrationCredentials>>(() => getDraft());
  const [isDraftLoaded, setIsDraftLoaded] = useState(false);

  // Load draft on mount
  useEffect(() => {
    setIsDraftLoaded(true);
  }, []);

  // Update setup state and sync with local draft persistence layer
  const updateSetupData = (
    newData:
      | Partial<Required<IntegrationCredentials>>
      | ((prev: Required<IntegrationCredentials>) => Required<IntegrationCredentials>)
  ) => {
    setSetupData((prev) => {
      const resolved = typeof newData === 'function' ? newData(prev) : { ...prev, ...newData };
      apiSaveDraft(resolved);
      return resolved;
    });
  };

  const saveDraft = () => {
    apiSaveDraft(setupData);
  };

  const clearDraft = () => {
    apiClearDraft();
    setSetupData({
      gmail: { ...DEFAULT_CREDENTIALS.gmail },
      slack: { ...DEFAULT_CREDENTIALS.slack },
      jira: { ...DEFAULT_CREDENTIALS.jira },
    });
  };

  return {
    setupData,
    updateSetupData,
    saveDraft,
    clearDraft,
    isDraftLoaded,
  };
}
