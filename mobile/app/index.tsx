import { useEffect } from "react";
import { View, ActivityIndicator } from "react-native";
import { router } from "expo-router";
import { getToken } from "@/lib/auth";
import { colors } from "@/lib/theme";

export default function Index() {
  useEffect(() => {
    (async () => {
      const token = await getToken();
      router.replace(token ? "/vehicles" : "/login");
    })();
  }, []);

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" }}>
      <ActivityIndicator color={colors.accent} />
    </View>
  );
}
