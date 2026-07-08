import { useEffect, useRef, useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, ActivityIndicator } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { router } from "expo-router";
import { plateResolve, pinLogin } from "@/lib/api";
import { saveSession } from "@/lib/auth";
import { initNotifications } from "@/lib/notifications";
import { colors } from "@/lib/theme";

type Phase = "scan" | "pin";

export default function Login() {
  const cameraRef = useRef<CameraView>(null);
  const [camPerm, requestCam] = useCameraPermissions();
  const [ready, setReady] = useState(false);
  const [phase, setPhase] = useState<Phase>("scan");
  const [manual, setManual] = useState(false); // typed the car number instead of scanning
  const [car, setCar] = useState("");
  const [name, setName] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    requestCam();
  }, []);

  async function scanPlate() {
    setError(null);
    if (!camPerm?.granted) {
      const r = await requestCam();
      if (!r.granted) {
        setError("Camera permission is needed to scan your plate. Or enter your car number below.");
        return;
      }
    }
    setBusy(true);
    try {
      const photo = await cameraRef.current?.takePictureAsync({ base64: true, quality: 0.5 });
      if (!photo?.base64) throw new Error("Could not capture the plate photo. Try again.");
      const res = await plateResolve(photo.base64);
      setCar(res.car_number);
      setName(res.name);
      setManual(false);
      setPin("");
      setPhase("pin");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not read the plate. Try again or enter it manually.");
    } finally {
      setBusy(false);
    }
  }

  function enterManually() {
    setManual(true);
    setName(null);
    setCar("");
    setPin("");
    setError(null);
    setPhase("pin");
  }

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

  if (phase === "pin") {
    return (
      <View style={styles.container}>
        <Text style={styles.brand}>BluCheck</Text>
        {name ? (
          <Text style={styles.welcome}>Welcome back, {name}</Text>
        ) : (
          <Text style={styles.subtitle}>Enter your car number and PIN</Text>
        )}

        {manual ? (
          <>
            <Text style={styles.label}>Car number</Text>
            <TextInput
              style={styles.input}
              placeholder="e.g. MH12AB4321"
              placeholderTextColor={colors.textDim}
              autoCapitalize="characters"
              value={car}
              onChangeText={setCar}
            />
          </>
        ) : (
          <View style={styles.carChip}>
            <Text style={styles.carChipText}>{car}</Text>
          </View>
        )}

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

        <Pressable
          style={styles.link}
          onPress={() => {
            setPhase("scan");
            setManual(false);
            setPin("");
            setError(null);
          }}
        >
          <Text style={styles.linkText}>Back to scan</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.brand}>BluCheck</Text>
      <Text style={styles.subtitle}>Scan your car's number plate to sign in</Text>

      <View style={styles.cameraWrap}>
        {camPerm?.granted ? (
          <CameraView
            ref={cameraRef}
            style={styles.camera}
            facing="back"
            onCameraReady={() => setReady(true)}
          />
        ) : (
          <View style={[styles.camera, styles.camPlaceholder]}>
            <Text style={styles.dim}>Allow camera access to scan your plate</Text>
          </View>
        )}
        <View style={styles.plateGuide} pointerEvents="none" />
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={scanPlate} disabled={busy || (!!camPerm?.granted && !ready)}>
        {busy ? <ActivityIndicator color="#ffffff" /> : <Text style={styles.buttonText}>Scan number plate</Text>}
      </Pressable>

      <Pressable style={styles.link} onPress={enterManually}>
        <Text style={styles.linkText}>Enter car number manually</Text>
      </Pressable>
      <Pressable style={styles.link} onPress={() => router.push("/register")}>
        <Text style={styles.linkText}>New driver? Register your car</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, padding: 24, justifyContent: "center" },
  brand: { color: colors.text, fontSize: 32, fontWeight: "700", letterSpacing: 1 },
  subtitle: { color: colors.textDim, marginBottom: 24, marginTop: 4 },
  welcome: { color: colors.text, fontSize: 18, fontWeight: "600", marginBottom: 20, marginTop: 4 },
  cameraWrap: {
    height: 220,
    borderRadius: 12,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: 16,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#000",
  },
  camera: { ...StyleSheet.absoluteFillObject },
  camPlaceholder: { backgroundColor: colors.surface, justifyContent: "center", alignItems: "center" },
  plateGuide: {
    width: "78%",
    height: 74,
    borderWidth: 2,
    borderColor: "#ffffffcc",
    borderRadius: 8,
    borderStyle: "dashed",
  },
  input: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 6, color: colors.text, padding: 14, marginBottom: 12 },
  pinInput: { fontSize: 24, letterSpacing: 12, textAlign: "center", fontFamily: colors.mono },
  label: { color: colors.textDim, fontSize: 13, marginBottom: 6 },
  carChip: { alignSelf: "flex-start", backgroundColor: colors.surface, borderColor: colors.accent, borderWidth: 1, borderRadius: 6, paddingVertical: 8, paddingHorizontal: 14, marginBottom: 18 },
  carChipText: { color: colors.text, fontFamily: colors.mono, fontSize: 18, letterSpacing: 2 },
  button: { backgroundColor: colors.accent, borderRadius: 6, padding: 16, alignItems: "center", marginTop: 8 },
  buttonText: { color: "#ffffff", fontWeight: "700" },
  link: { padding: 14, alignItems: "center", marginTop: 4 },
  linkText: { color: colors.accent },
  error: { color: colors.danger, marginBottom: 8 },
  dim: { color: colors.textDim, textAlign: "center", paddingHorizontal: 20 },
});
