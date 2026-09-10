/**
 * Test entry points for the three screens.
 *
 * A named re-export rather than importing the screen modules directly, so a
 * test's dependency on internal structure is visible in one place: if the
 * screens move again, one file changes rather than every test.
 */

export { BlueprintScreen as BlueprintScreenForTest } from "@/screens/BlueprintScreen";
export { DatasetsScreen as DatasetsScreenForTest } from "@/screens/DatasetsScreen";
export { RunsScreen as RunsScreenForTest } from "@/screens/RunsScreen";
