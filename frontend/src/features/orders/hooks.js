import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/features/auth/store.js';
import { ordersApi } from './api.js';

// Query keys shared by OrdersPage / OrderDetailPage. Detail ids arrive as
// route-param strings — keep that shape so invalidation + setQueryData match.
const LIST_KEY = ['orders'];
const DETAIL_KEY = (id) => ['order', String(id)];

export function useMyOrders() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: LIST_KEY,
    queryFn: ordersApi.listMine,
    enabled: !!token,
    retry: false,
  });
}

export function useOrderDetail(id) {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: DETAIL_KEY(id),
    queryFn: () => ordersApi.getMine(id),
    enabled: !!token && !!id,
    retry: false,
  });
}

export function useCancelOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }) => ordersApi.cancel(id, reason),
    onSuccess: (updated, vars) => {
      // Show the new status immediately, then refetch for truth.
      qc.setQueryData(DETAIL_KEY(vars.id), updated);
      qc.invalidateQueries({ queryKey: LIST_KEY });
      qc.invalidateQueries({ queryKey: ['order'] });
    },
  });
}

function filenameFromDisposition(disposition) {
  if (!disposition) return null;
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return m[1];
  }
}

/**
 * Fetches the invoice PDF with the auth header and hands it to the browser as
 * a download. A plain <a href> would arrive without Authorization and 401, so
 * we go blob → object URL → synthetic <a download> click.
 */
export function useDownloadInvoice() {
  return useMutation({
    mutationFn: async (id) => {
      const resp = await ordersApi.invoice(id);
      const filename =
        filenameFromDisposition(resp.headers?.['content-disposition']) ||
        `invoice-order-${id}.pdf`;

      const blob = new Blob([resp.data], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Give the browser a beat to start the download before revoking.
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      return filename;
    },
  });
}
