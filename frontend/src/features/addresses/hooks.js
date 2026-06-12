import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { addressApi } from './api.js';
import { useAuthStore } from '@/features/auth/store.js';

/**
 * Maps a geolocation / custom error to a human-friendly string.
 * Never throws.
 */
export function friendlyGeoError(error) {
  if (!error) return 'Could not determine your location.';
  // Custom codes from getDevicePosition
  if (error.code === 'unsupported') {
    return "Your browser doesn't support location detection.";
  }
  if (error.code === 'insecure') {
    return 'Location detection needs a secure (https) connection.';
  }
  // GeolocationPositionError codes (1 = PERMISSION_DENIED, 2 = POSITION_UNAVAILABLE, 3 = TIMEOUT)
  if (error.code === 1) {
    return 'Location permission denied — allow location access in your browser, or enter your pincode manually.';
  }
  if (error.code === 2 || error.code === 3) {
    return "Couldn't determine your location. Please enter your pincode manually.";
  }
  return "Couldn't determine your location. Please enter your pincode manually.";
}

/**
 * Raw promise that resolves to { latitude, longitude } from the browser
 * Geolocation API. Rejects with a typed error ({ code: string | number })
 * that `friendlyGeoError` understands.
 *
 * Shared by useCurrentLocation (mutation) and MapAddressPicker (direct call).
 */
export function getDevicePosition() {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject({ code: 'unsupported' });
      return;
    }
    if (!window.isSecureContext) {
      reject({ code: 'insecure' });
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ latitude: pos.coords.latitude, longitude: pos.coords.longitude }),
      (err) => reject(err),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
    );
  });
}

/**
 * User-triggered geolocation → reverse geocode.
 * Use as a mutation (call `.mutate()` / `.mutateAsync()` on user action).
 * Returns the reverseGeocode response: { found, pincode, city, state, area, road }.
 */
export function useCurrentLocation() {
  return useMutation({
    mutationFn: () =>
      getDevicePosition().then((coords) =>
        addressApi
          .reverseGeocode(coords.latitude, coords.longitude)
          .then((geocodeResult) => ({
            ...geocodeResult,
            lat: coords.latitude,
            lng: coords.longitude,
          })),
      ),
    retry: false,
  });
}

const ADDR_KEY = ['addresses'];

function useInvalidateAddresses() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ADDR_KEY });
}

/** Fetch the signed-in user's saved addresses. */
export function useAddresses() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: ADDR_KEY,
    queryFn: addressApi.list,
    enabled: !!token,
    retry: false,
  });
}

export function useCreateAddress() {
  const invalidate = useInvalidateAddresses();
  return useMutation({
    mutationFn: (data) => addressApi.create(data),
    onSuccess: invalidate,
  });
}

export function useUpdateAddress() {
  const invalidate = useInvalidateAddresses();
  return useMutation({
    mutationFn: ({ id, data }) => addressApi.update(id, data),
    onSuccess: invalidate,
  });
}

export function useDeleteAddress() {
  const invalidate = useInvalidateAddresses();
  return useMutation({
    mutationFn: (id) => addressApi.remove(id),
    onSuccess: invalidate,
  });
}

export function useSetDefaultAddress() {
  const invalidate = useInvalidateAddresses();
  return useMutation({
    mutationFn: (id) => addressApi.setDefault(id),
    onSuccess: invalidate,
  });
}

/**
 * Auto-fill city/state from pincode.
 * Only fires when pincode matches the 6-digit Indian pin format.
 * Caches positives for 24h; never retries on failure.
 */
export function usePincodeLookup(pincode) {
  const clean = (pincode || '').trim();
  const enabled = /^[1-9][0-9]{5}$/.test(clean);
  return useQuery({
    queryKey: ['pincode', clean],
    queryFn: () => addressApi.lookupPincode(clean),
    enabled,
    staleTime: 24 * 60 * 60 * 1000, // 24h — postal data rarely changes
    retry: false,
  });
}
