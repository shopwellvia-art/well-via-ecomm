import { useMutation, useQueryClient } from '@tanstack/react-query';
import { adminApi } from './api.js';

function useProductInvalidation() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ['products'] });
    qc.invalidateQueries({ queryKey: ['product'] });
  };
}

export function useCreateProduct() {
  const invalidate = useProductInvalidation();
  return useMutation({
    mutationFn: (data) => adminApi.createProduct(data),
    onSuccess: invalidate,
  });
}

export function useUpdateProduct() {
  const invalidate = useProductInvalidation();
  return useMutation({
    mutationFn: ({ id, data }) => adminApi.updateProduct(id, data),
    onSuccess: invalidate,
  });
}

export function useDeleteProduct() {
  const invalidate = useProductInvalidation();
  return useMutation({
    mutationFn: (id) => adminApi.deleteProduct(id),
    onSuccess: invalidate,
  });
}

export function useUploadProductImages() {
  const invalidate = useProductInvalidation();
  return useMutation({
    mutationFn: ({ id, files }) => adminApi.uploadProductImages(id, files),
    onSuccess: invalidate,
  });
}

export function useDeleteProductImage() {
  const invalidate = useProductInvalidation();
  return useMutation({
    mutationFn: ({ id, imageId }) => adminApi.deleteProductImage(id, imageId),
    onSuccess: invalidate,
  });
}

export function useSetPrimaryImage() {
  const invalidate = useProductInvalidation();
  return useMutation({
    mutationFn: ({ id, imageId }) => adminApi.setPrimaryImage(id, imageId),
    onSuccess: invalidate,
  });
}

export function useReorderProductImages() {
  const invalidate = useProductInvalidation();
  return useMutation({
    mutationFn: ({ id, imageIds }) => adminApi.reorderProductImages(id, imageIds),
    onSuccess: invalidate,
  });
}
