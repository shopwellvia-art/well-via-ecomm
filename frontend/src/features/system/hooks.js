import { useMutation } from '@tanstack/react-query';
import { systemApi } from './api.js';

export function useTruncateDatabase() {
  return useMutation({
    mutationFn: (confirm) => systemApi.truncateDatabase(confirm),
  });
}
