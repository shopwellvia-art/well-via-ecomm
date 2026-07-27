import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { reviewsApi } from './api.js';

const productKey = (id, opts) => ['reviews', 'product', String(id), opts];
const adminKey = (opts) => ['reviews', 'admin', opts];

// Invalidate every shape after a write — review changes touch the public list,
// admin list, and the product's denormalized rating that ProductRead returns.
function useInvalidateAll() {
  const qc = useQueryClient();
  return (productId) => {
    qc.invalidateQueries({ queryKey: ['reviews'] });
    if (productId != null) {
      qc.invalidateQueries({ queryKey: ['product', String(productId)] });
      qc.invalidateQueries({ queryKey: ['product', Number(productId)] });
      qc.invalidateQueries({ queryKey: ['products'] });
    }
  };
}

export function useProductReviews(productId, opts = {}) {
  return useQuery({
    queryKey: productKey(productId, opts),
    queryFn: () => reviewsApi.listForProduct(productId, opts),
    enabled: !!productId,
    placeholderData: keepPreviousData,
  });
}

export function useCreateReview() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: ({ productId, data }) => reviewsApi.create(productId, data),
    onSuccess: (_data, vars) => invalidate(vars.productId),
  });
}

export function useUpdateOwnReview() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: ({ reviewId, data }) => reviewsApi.updateOwn(reviewId, data),
    onSuccess: (data) => invalidate(data?.product_id),
  });
}

export function useDeleteOwnReview() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: ({ reviewId }) => reviewsApi.removeOwn(reviewId),
    onSuccess: (_data, vars) => invalidate(vars.productId),
  });
}

// ----- Admin -----

export function useAdminReviews(opts = {}) {
  return useQuery({
    queryKey: adminKey(opts),
    queryFn: () => reviewsApi.adminList(opts),
    placeholderData: keepPreviousData,
  });
}

export function useAdminCreateReview() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (data) => reviewsApi.adminCreate(data),
    onSuccess: (data) => invalidate(data?.product_id),
  });
}

export function useAdminUpdateReview() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: ({ reviewId, data }) => reviewsApi.adminUpdate(reviewId, data),
    onSuccess: (data) => invalidate(data?.product_id),
  });
}

export function useAdminDeleteReview() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: ({ reviewId }) => reviewsApi.adminRemove(reviewId),
    onSuccess: (_data, vars) => invalidate(vars.productId),
  });
}
