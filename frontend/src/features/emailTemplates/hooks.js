import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { emailTemplatesApi } from './api.js';

const LIST_KEY = ['email-templates'];
const detailKey = (key) => ['email-templates', key];

/** List all templates (summary rows, no body). */
export function useEmailTemplates() {
  return useQuery({
    queryKey: LIST_KEY,
    queryFn: emailTemplatesApi.list,
  });
}

/** Full detail for a single template including body_html, body_design, variables. */
export function useEmailTemplate(key) {
  return useQuery({
    queryKey: detailKey(key),
    queryFn: () => emailTemplatesApi.get(key),
    enabled: Boolean(key),
  });
}

/** Save subject / body_html / body_design / is_enabled changes. */
export function useUpdateTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, payload }) => emailTemplatesApi.update(key, payload),
    onSuccess: (data, { key }) => {
      // Update the detail cache directly so the editor re-seeds from saved data.
      qc.setQueryData(detailKey(key), data);
      // Refresh the list so the enabled-dot and name reflect any changes.
      qc.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
}

/** Render a preview (unsaved draft). Returns { subject, html }. */
export function usePreviewTemplate() {
  return useMutation({
    mutationFn: ({ key, draft }) => emailTemplatesApi.preview(key, draft),
  });
}

/** Send a test delivery. Returns { detail }. */
export function useTestTemplate() {
  return useMutation({
    mutationFn: ({ key, to }) => emailTemplatesApi.test(key, to),
  });
}

/** Reset a template to its built-in default. Returns the detail object. */
export function useResetTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key) => emailTemplatesApi.reset(key),
    onSuccess: (data, key) => {
      qc.setQueryData(detailKey(key), data);
      qc.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
}
