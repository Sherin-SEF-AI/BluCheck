import { useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, ActivityIndicator, ScrollView } from "react-native";
import { router } from "expo-router";
import { register } from "@/lib/api";
import { saveSession } from "@/lib/auth";
import { initNotifications } from "@/lib/notifications";
import { colors } from "@/lib/theme";

export default function Register() {
  const [name, setName] = useState("");
  const [car, setCar] = useState("");
  const [pin, setPin] = useState("");
  const [pin2, setPin2] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit() {
    const cleanName = name.trim();
    const cleanCar = car.trim().toUpperCase().replace(/\s/g, "");
    if (!cleanName || cleanCar.length < 2) {
      setError("Enter your name and car number.");
      return;
    }
    if (pin.length !== 4) {
      setError("Choose a 4-digit PIN.");
      return;
    }
    if (pin !== pin2) {
      setError("The two PINs do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await register(cleanName, cleanCar, pin);
      await saveSession(res.access_token, res.role, res.name, res.car_number ?? cleanCar);
      // Prompt for notification permission + register the push token right after sign-up.
      initNotifications().catch(() => undefined);
      router.replace("/vehicles");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Text style={styles.title}>Register</Text>
      <Text style={styles.subtitle}>One driver, one car. You sign in by scanning your plate and entering this PIN.</Text>

      <Text style={styles.label}>Full name</Text>
      <TextInput
        style={styles.input}
        placeholder="e.g. Ramesh Kumar"
        placeholderTextColor={colors.textDim}
        value={name}
        onChangeText={setName}
      />

      <Text style={styles.label}>Car number</Text>
      <TextInput
        style={styles.input}
        placeholder="e.g. MH12AB4321"
        placeholderTextColor={colors.textDim}
        autoCapitalize="characters"
        value={car}
        onChangeText={setCar}
      />

      <Text style={styles.label}>Choose a 4-digit PIN</Text>
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

      <Text style={styles.label}>Confirm PIN</Text>
      <TextInput
        style={[styles.input, styles.pinInput]}
        placeholder="••••"
        placeholderTextColor={colors.textDim}
        keyboardType="number-pad"
        secureTextEntry
        maxLength={4}
        value={pin2}
        onChangeText={(t) => setPin2(t.replace(/\D/g, ""))}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={onSubmit} disabled={busy}>
        {busy ? <ActivityIndicator color="#ffffff" /> : <Text style={styles.buttonText}>Create account</Text>}
      </Pressable>

      <Pressable style={styles.link} onPress={() => router.back()}>
        <Text style={styles.linkText}>Already registered? Sign in</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { backgroundColor: colors.bg, padding: 24, flexGrow: 1, justifyContent: "center" },
  title: { color: colors.text, fontSize: 26, fontWeight: "700" },
  subtitle: { color: colors.textDim, marginBottom: 24, marginTop: 4 },
  label: { color: colors.textDim, fontSize: 12, marginBottom: 6, fontFamily: colors.mono },
  input: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 6, color: colors.text, padding: 14, marginBottom: 16 },
  pinInput: { fontSize: 22, letterSpacing: 10, textAlign: "center", fontFamily: colors.mono },
  button: { backgroundColor: colors.accent, borderRadius: 6, padding: 16, alignItems: "center", marginTop: 8 },
  buttonText: { color: "#ffffff", fontWeight: "700" },
  link: { padding: 14, alignItems: "center", marginTop: 8 },
  linkText: { color: colors.accent },
  error: { color: colors.danger, marginBottom: 8 },
});
