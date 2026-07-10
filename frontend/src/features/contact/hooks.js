import { useMutation } from '@tanstack/react-query';
import { contactApi } from './api.js';

/** Submit a general enquiry through the public contact endpoint. */
export function useSubmitContactMessage() {
  return useMutation({
    mutationFn: (data) => contactApi.submitMessage(data),
  });
}

/** Subscribe an email address to the newsletter (footer + contact page). */
export function useSubscribeNewsletter() {
  return useMutation({
    mutationFn: (email) => contactApi.subscribeNewsletter(email),
  });
}
