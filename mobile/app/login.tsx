import { useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, ActivityIndicator } from "react-native";
import { router } from "expo-router";
import { pinLogin } from "@/lib/api";
import { saveSession } from "@/lib/auth";
import { initNotifications } from "@/lib/notifications";
import { colors } from "@/lib/theme";

// Plate-scan sign-in is disabled for now (OCR to be re-added later); drivers sign in with their
// car number and 4-digit PIN.
export default function Login() {
  const [car, setCar] = useState("");
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submitPin() {
    const c = car.trim().toUpperCase().replace(/\s/g, "");
    if (c.length < 2) {
      setError("Enter your car number.");
      return;
    }
    if (pin.length !== 4) {
      setError("Enter your 4-digit PIN.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await pinLogin(c, pin);
      await saveSession(res.access_token, res.role, res.name, res.car_number ?? c);
      // Register for notifications now that we are signed in: prompts for permission and
      // registers the push token. Runs here (not just at app launch) so a fresh install/login
      // still gets the prompt. Non-blocking so it never delays navigation.
      initNotifications().catch(() => undefined);
      router.replace("/vehicles");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.brand}>BluCheck</Text>
      <Text style={styles.subtitle}>Enter your car number and PIN</Text>

      <Text style={styles.label}>Car number</Text>
      <TextInput
        style={styles.input}
        placeholder="e.g. MH12AB4321"
        placeholderTextColor={colors.textDim}
        autoCapitalize="characters"
        autoCorrect={false}
        value={car}
        onChangeText={setCar}
      />

      <Text style={styles.label}>4-digit PIN</Text>
      <TextInput
        style={[styles.input, styles.pinInput]}
        placeholder="••••"
        placeholderTextColor={colors.textDim}
        keyboardType="number-pad"
        secureTextEntry
        maxLength={4}
        value={pin}
        onChangeText={(t) => setPin(t.replace(/\D/g, ""))}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={submitPin} disabled={busy}>
        {busy ? <ActivityIndicator color="#ffffff" /> : <Text style={styles.buttonText}>Sign in</Text>}
      </Pressable>

      <Pressable style={styles.link} onPress={() => router.push("/register")}>
        <Text style={styles.linkText}>New driver? Register your car</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, padding: 24, justifyContent: "center" },
  brand: { color: colors.text, fontSize: 30, fontWeight: "800", textAlign: "center", letterSpacing: 1 },
  subtitle: { color: colors.textDim, textAlign: "center", marginTop: 8, marginBottom: 28 },
  label: { color: colors.textDim, fontFamily: colors.mono, fontSize: 12, letterSpacing: 1, marginBottom: 6, marginTop: 12 },
  input: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 14, color: colors.text, fontSize: 16, fontFamily: colors.mono, letterSpacing: 1 },
  pinInput: { letterSpacing: 8, textAlign: "center", fontSize: 22 },
  error: { color: colors.danger, marginTop: 14, textAlign: "center" },
  button: { backgroundColor: colors.accent, borderRadius: 8, padding: 16, alignItems: "center", marginTop: 22 },
  buttonText: { color: "#ffffff", fontWeight: "700", fontSize: 16 },
  link: { padding: 14, alignItems: "center", marginTop: 6 },
  linkText: { color: colors.accent },
});
