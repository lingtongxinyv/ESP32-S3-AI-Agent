package asia.lingtong.agent.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// —— 「蔚蓝档案」色板：天空蓝 + 深海军蓝 + 纯白卡片 + 淡蓝底 ——
val BaSky = Color(0xFF46A8F0)
val BaSkyDeep = Color(0xFF2B84D6)
val BaSkyDark = Color(0xFF1B5FA8)
val BaSkyLight = Color(0xFFDDEEFF)
val BaNavy = Color(0xFF1E3355)
val BaNavySoft = Color(0xFF5B7396)
val BaBg = Color(0xFFEAF3FB)
val BaBgTop = Color(0xFFF7FBFF)
val BaLine = Color(0xFFC7DEF2)
val BaLineSoft = Color(0xFFE2EEF9)
val BaOrange = Color(0xFFFFB454)
val BaPink = Color(0xFFFF93B4)
val BaMint = Color(0xFF7FDCC7)

private val LightColors = lightColorScheme(
    primary = BaSkyDeep,
    onPrimary = Color.White,
    primaryContainer = BaSkyLight,
    onPrimaryContainer = BaNavy,
    secondary = BaSky,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFEAF5FF),
    onSecondaryContainer = BaNavy,
    tertiary = BaOrange,
    onTertiary = BaNavy,
    background = BaBg,
    onBackground = BaNavy,
    surface = Color.White,
    onSurface = BaNavy,
    surfaceVariant = Color(0xFFF3F9FF),
    onSurfaceVariant = BaNavySoft,
    outline = BaLine,
    outlineVariant = BaLineSoft,
    error = Color(0xFFE05A6E),
    onError = Color.White,
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF7CC4F7),
    onPrimary = Color(0xFF0A2A47),
    primaryContainer = Color(0xFF1D3F61),
    onPrimaryContainer = Color(0xFFD6ECFF),
    secondary = Color(0xFF9BD4F7),
    onSecondary = Color(0xFF0A2233),
    tertiary = BaOrange,
    background = Color(0xFF0F1B2B),
    onBackground = Color(0xFFE6F1FB),
    surface = Color(0xFF162437),
    onSurface = Color(0xFFE6F1FB),
    surfaceVariant = Color(0xFF1C2C42),
    onSurfaceVariant = Color(0xFFA8C2DC),
    outline = Color(0xFF36506C),
    outlineVariant = Color(0xFF24384F),
    error = Color(0xFFFF8B9C),
)

private val BaTypography = Typography(
    displaySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Black,
        fontSize = 32.sp,
        lineHeight = 38.sp,
        letterSpacing = 1.sp,
    ),
    headlineMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Black,
        fontSize = 26.sp,
        lineHeight = 32.sp,
        letterSpacing = 0.8.sp,
    ),
    headlineSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Black,
        fontSize = 22.sp,
        lineHeight = 28.sp,
        letterSpacing = 0.6.sp,
    ),
    titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Black,
        fontSize = 20.sp,
        lineHeight = 26.sp,
        letterSpacing = 1.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 17.sp,
        lineHeight = 23.sp,
        letterSpacing = 0.5.sp,
    ),
    titleSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 15.sp,
        lineHeight = 21.sp,
        letterSpacing = 0.8.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 23.sp,
        letterSpacing = 0.2.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 21.sp,
        letterSpacing = 0.2.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 18.sp,
        letterSpacing = 0.3.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 14.sp,
        lineHeight = 19.sp,
        letterSpacing = 0.8.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 12.sp,
        lineHeight = 16.sp,
        letterSpacing = 1.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 10.sp,
        lineHeight = 14.sp,
        letterSpacing = 1.6.sp,
    ),
)

private val BaShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(18.dp),
    extraLarge = RoundedCornerShape(24.dp),
)

@Composable
fun AgentTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors,
        typography = BaTypography,
        shapes = BaShapes,
        content = content,
    )
}
