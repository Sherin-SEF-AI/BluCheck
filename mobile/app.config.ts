import { ExpoConfig, ConfigContext } from "expo/config";

// BluCheck driver app. API base URL is provided via EAS env or app config extra.
export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: "BluCheck",
  slug: "blucheck",
  scheme: "blucheck",
  version: "1.0.0",
  orientation: "portrait",
  userInterfaceStyle: "light",
  newArchEnabled: true,
  icon: "./assets/icon.png",
  splash: {
    image: "./assets/icon.png",
    resizeMode: "contain",
    backgroundColor: "#ffffff",
  },
  ios: {
    supportsTablet: false,
    bundleIdentifier: "com.blurabbit.blucheck",
    infoPlist: {
      NSCameraUsageDescription:
        "BluCheck records short exterior and interior videos of your vehicle for cleanliness inspection.",
      NSMicrophoneUsageDescription:
        "Audio is captured alongside the inspection video.",
      NSLocationWhenInUseUsageDescription:
        "BluCheck records the GPS location where each inspection video is captured.",
    },
  },
  android: {
    package: "com.blurabbit.blucheck",
    googleServicesFile: "./google-services.json",
    adaptiveIcon: {
      foregroundImage: "./assets/adaptive-icon.png",
      backgroundColor: "#1a3ca2",
    },
    permissions: [
      "CAMERA",
      "RECORD_AUDIO",
      "ACCESS_FINE_LOCATION",
      "ACCESS_COARSE_LOCATION",
    ],
  },
  plugins: [
    "expo-router",
    "expo-secure-store",
    ["expo-camera", { cameraPermission: "BluCheck needs the camera to record inspection videos." }],
    ["expo-location", { locationWhenInUsePermission: "BluCheck records where each inspection is captured." }],
    ["expo-notifications", { color: "#4a9d8e" }],
  ],
  extra: {
    apiBaseUrl: process.env.API_BASE_URL ?? "https://d1sc0mm026oa3r.cloudfront.net",
    router: { origin: false },
  },
});
