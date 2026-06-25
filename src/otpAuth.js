import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

export const supabase = SUPABASE_URL && SUPABASE_ANON_KEY
  ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY)
  : null;

export function assertSupabaseConfigured() {
  if (!supabase) {
    throw new Error("Supabase env vars not set (VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_KEY)");
  }
}

export async function signInWithPhoneOtp(phone, options = {}) {
  assertSupabaseConfigured();
  // Supabase expects E.164 numbers, e.g. +14155552671
  const { data, error } = await supabase.auth.signInWithOtp({
    phone,
    options: {
      // You can optionally force channel / redirect behavior here.
      // Default is email/phone based on your Supabase configuration.
      ...options,
    },
  });
  if (error) throw error;
  return data;
}

export async function signInWithPassword(email, password) {
  assertSupabaseConfigured();
  const { data, error } = await supabase.auth.signInWithPassword({
    email: email.trim(),
    password,
  });
  if (error) throw error;
  return data;
}

export async function signUpWithPassword(email, password, fullName = "") {
  assertSupabaseConfigured();
  const { data, error } = await supabase.auth.signUp({
    email: email.trim(),
    password,
    options: {
      data: { full_name: fullName.trim() },
    },
  });
  if (error) throw error;
  return data;
}

export async function verifyPhoneOtp(otp, phone) {
  assertSupabaseConfigured();
  // For phone OTP, Supabase SDK uses verifyOtp with type 'sms' by default.
  const { data, error } = await supabase.auth.verifyOtp({
    phone,
    token: otp,
    type: "sms",
  });
  if (error) throw error;
  return data;
}

export async function getSession() {
  assertSupabaseConfigured();
  const { data, error } = await supabase.auth.getSession();
  if (error) throw error;
  return data.session;
}

export function onAuthStateChange(callback) {
  assertSupabaseConfigured();
  return supabase.auth.onAuthStateChange((_event, session) => callback(session));
}

export async function signOut() {
  assertSupabaseConfigured();
  const { error } = await supabase.auth.signOut();
  if (error) throw error;
}

export async function getAccessToken() {
  assertSupabaseConfigured();
  const { data } = await supabase.auth.getSession();
  const token = data?.session?.access_token;
  if (!token) throw new Error("Not authenticated. Please sign in.");
  return token;
}
