"use client";

import { useMutation } from "@tanstack/react-query";

import { useAuth } from "@/components/auth-provider";
import { updateSelectedModel } from "@/lib/api";

export function useUpdateModel() {
  const { user, setUser } = useAuth();
  return useMutation({
    mutationFn: updateSelectedModel,
    // Optimistic update: reflect the new selection immediately rather
    // than waiting for the round-trip, so a fast select-then-send can't
    // race a chat message out the door against the still-stale model_id
    // shown in this same session (the backend independently re-fetches
    // the user's current model on every turn regardless -- see
    // ChatService._run_generation -- this is purely about the frontend's
    // own display/state staying in sync with what was just clicked).
    onMutate: async (modelId: string) => {
      const previousUser = user;
      if (previousUser) {
        setUser({ ...previousUser, model_id: modelId });
      }
      return { previousUser };
    },
    onSuccess: (profile) => setUser(profile),
    onError: (_err, _modelId, context) => {
      if (context?.previousUser) {
        setUser(context.previousUser);
      }
    },
  });
}
