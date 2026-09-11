"use client";

import { useMutation } from "@tanstack/react-query";

import { useAuth } from "@/components/auth-provider";
import { updateSelectedModel } from "@/lib/api";

export function useUpdateModel() {
  const { setUser } = useAuth();
  return useMutation({
    mutationFn: updateSelectedModel,
    onSuccess: (profile) => setUser(profile),
  });
}
