import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/lib/api";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: true,
        retry: (count, err) => {
          if (err instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(err.status)) return false;
          return count < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}
