package asia.lingtong.agent.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import asia.lingtong.agent.ui.theme.BaLine
import asia.lingtong.agent.ui.theme.BaLineSoft
import asia.lingtong.agent.ui.theme.BaNavy
import asia.lingtong.agent.ui.theme.BaNavySoft
import asia.lingtong.agent.ui.theme.BaSky
import asia.lingtong.agent.ui.theme.BaSkyDeep

/** 分区卡片：白底、细蓝边、右上/左下切角，标题前带倾斜小方块。 */
@Composable
fun SectionCard(
    title: String,
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = rememberCutShape(14.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, BaLine),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(7.dp)
                        .rotate(45f)
                        .background(BaSkyDeep, RoundedCornerShape(1.dp)),
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleSmall,
                    color = BaNavy,
                )
                Spacer(modifier = Modifier.weight(1f))
                BaStripes(
                    modifier = Modifier.size(width = 34.dp, height = 9.dp),
                    color = BaSky.copy(alpha = 0.5f),
                )
            }
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(1.dp)
                    .background(BaLineSoft),
            )
            content()
        }
    }
}

/** 说明文字：前置一条蓝色短横线，弱化存在感。 */
@Composable
fun Hint(text: String, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Box(
            modifier = Modifier
                .padding(top = 7.dp)
                .size(width = 8.dp, height = 2.dp)
                .background(BaSky, RoundedCornerShape(1.dp)),
        )
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = BaNavySoft,
        )
    }
}

/** 居中的空状态提示。 */
@Composable
fun BaEmptyState(title: String, hint: String, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier,
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        BaHalo(modifier = Modifier.size(96.dp))
        Text(
            text = title,
            style = MaterialTheme.typography.titleSmall,
            color = BaNavy,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
        Text(
            text = hint,
            style = MaterialTheme.typography.bodySmall,
            color = BaNavySoft,
            textAlign = TextAlign.Center,
        )
    }
}
