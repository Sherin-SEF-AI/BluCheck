import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "blucheck.jwt";
const ROLE_KEY = "blucheck.role";
const NAME_KEY = "blucheck.name";
const CAR_KEY = "blucheck.car";

export async function saveSession(token: string, role: string, name = "", car = ""): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token);
  await SecureStore.setItemAsync(ROLE_KEY, role);
  await SecureStore.setItemAsync(NAME_KEY, name);
  await SecureStore.setItemAsync(CAR_KEY, car || "");
}

export async function getToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY);
}
export async function getRole(): Promise<string | null> {
  return SecureStore.getItemAsync(ROLE_KEY);
}
export async function getName(): Promise<string | null> {
  return SecureStore.getItemAsync(NAME_KEY);
}
export async function getCar(): Promise<string | null> {
  return SecureStore.getItemAsync(CAR_KEY);
}

export async function clearSession(): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
  await SecureStore.deleteItemAsync(ROLE_KEY);
  await SecureStore.deleteItemAsync(NAME_KEY);
  await SecureStore.deleteItemAsync(CAR_KEY);
}
