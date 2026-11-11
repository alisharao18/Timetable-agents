/**
 * MPSTME timetable: send every edit on this Google Sheet to the central database.
 *
 * Setup (once, by the sheet owner) - full steps are in SETUP.md:
 *   1. Extensions > Apps Script, paste this file, save.
 *   2. Project Settings > Script Properties, add:
 *        DB_URL   your Supabase project URL, e.g. https://abcd1234.supabase.co
 *        DB_KEY   the Supabase service_role key (keep it secret; never put it in a cell)
 *   3. Triggers > Add Trigger > function: sendEdit, event: From spreadsheet, On edit.
 *
 * Works on both layouts: the classroom sheet (one tab per day, one column per
 * room) and the lab sheet (one tab per floor, one block per lab).
 * The script only REPORTS edits. The app decides whether each one is applied
 * or rejected, so a typo here can never overwrite an existing booking.
 */
var DAY_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

function sendEdit(e) {
  if (!e || !e.range) return;
  var sheet = e.range.getSheet();
  var rows = e.range.getNumRows(), cols = e.range.getNumColumns();
  var values = e.range.getValues();
  var payload = [];
  for (var i = 0; i < rows; i++) {
    for (var j = 0; j < cols; j++) {
      var cell = locate(sheet, e.range.getRow() + i, e.range.getColumn() + j);
      if (!cell) continue;
      payload.push({
        at: new Date().toISOString(),
        sheet: sheet.getName(),
        room: cell.room, day: cell.day, hour: cell.hour,
        old_text: rows * cols === 1 && e.oldValue !== undefined ? String(e.oldValue) : null,
        new_text: values[i][j] === '' ? null : String(values[i][j]),
        editor: e.user && e.user.getEmail ? e.user.getEmail() : null,
        status: 'pending'
      });
    }
  }
  if (!payload.length) return;
  var props = PropertiesService.getScriptProperties();
  UrlFetchApp.fetch(props.getProperty('DB_URL') + '/rest/v1/sheet_edits', {
    method: 'post',
    contentType: 'application/json',
    headers: { apikey: props.getProperty('DB_KEY'),
               Authorization: 'Bearer ' + props.getProperty('DB_KEY'),
               Prefer: 'return=minimal' },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
}

/** 8, "12 :: 1", "1pm-2pm", "3 to 4"  ->  the starting hour on a 24-hour clock. */
function parseHour(label) {
  if (label instanceof Date) { var h0 = label.getHours(); return h0 < 7 ? h0 + 12 : h0; }
  var m = String(label).match(/^\s*(\d{1,2})/);
  if (!m) return null;
  var h = parseInt(m[1], 10);
  if (h < 7) h += 12;
  return (h >= 7 && h <= 18) ? h : null;
}

function dayOf(text) {
  var t = String(text || '').trim().toLowerCase();
  for (var i = 0; i < DAY_NAMES.length; i++) {
    if (t.indexOf(DAY_NAMES[i].substring(0, 3)) === 0) return DAY_NAMES[i].substring(0, 1).toUpperCase() + DAY_NAMES[i].substring(1, 3);
  }
  return null;
}

/** Work out which room, day and hour a cell belongs to. Returns null outside a grid. */
function locate(sheet, row, col) {
  var name = sheet.getName().trim().toLowerCase();
  // Classroom layout: the tab is the day; row 2 holds room numbers; column A holds times.
  if (DAY_NAMES.indexOf(name) >= 0) {
    if (row < 6 || col < 2) return null;
    var room = String(sheet.getRange(2, col).getValue()).trim();
    var hour = parseHour(sheet.getRange(row, 1).getValue());
    if (!room || hour === null) return null;
    return { room: room, day: dayOf(name), hour: hour };
  }
  // Lab layout: find the block header ("Time | Monday | Tuesday ...") above this cell.
  if (name.indexOf('floor') < 0) return null;
  var top = Math.max(1, row - 14);
  var block = sheet.getRange(top, 1, row - top + 1, Math.max(col, 2)).getValues();
  for (var r = block.length - 2; r >= 0; r--) {
    var day = dayOf(block[r][col - 1]);
    if (!day) continue;
    var timeCol = -1;
    for (var c = col - 2; c >= Math.max(0, col - 8); c--) {
      if (String(block[r][c]).trim().toLowerCase() === 'time') { timeCol = c; break; }
    }
    if (timeCol < 0) return null;
    var hr = parseHour(block[block.length - 1][timeCol]);
    if (hr === null) return null;
    var title = '';
    if (top + r - 1 >= 1) {
      var above = sheet.getRange(top + r - 1, timeCol + 1, 1, 7).getValues()[0];
      for (var k = 0; k < above.length; k++) { if (String(above[k]).trim().length > 3) { title = String(above[k]); break; } }
    }
    if (!title) return null;
    return { room: title, day: day, hour: hr };
  }
  return null;
}
