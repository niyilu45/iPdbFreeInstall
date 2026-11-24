" System Vim bridge. Loaded after the user's vimrc and plugins.
if !exists('*timer_start') || !exists('*json_decode') || !exists('*win_getid')
  call writefile(['System Vim requires timers, JSON and window IDs'], $IPDB_VIM_READY)
  finish
endif
" Keep recovery files, but do not share swap files with other editor sessions.
" An E325 prompt can consume navigation/input before Normal-mode mappings run.
let s:swap_directory = expand('~/.cache/ipdb/vim-swap/') . getpid()
try
  call mkdir(s:swap_directory, 'p', 0700)
  let &directory = escape(s:swap_directory, ' ,') . '//'
catch
  echohl WarningMsg
  echom 'ipdb: could not create a private Vim swap directory: ' . v:exception
  echohl None
endtry
" Enable syntax by default without resetting an existing color scheme.
if has('syntax') && !exists('g:syntax_on')
  syntax enable
endif
" Route clicks to the corresponding Vim window instead of terminal selection.
set mouse=a
" Successful multi-buffer writes must not enter a hit-enter/more prompt.
" Keep write errors visible (silent, not silent!) and leave other Ex output alone.
cnoreabbrev <expr> wa getcmdtype() ==# ':' && getcmdline() ==# 'wa' ? 'silent wall' : 'wa'
cnoreabbrev <expr> wall getcmdtype() ==# ':' && getcmdline() ==# 'wall' ? 'silent wall' : 'wall'
" Avoid terminal line-scroll and background-erase optimizations in tmux.
" Some outer terminals leave cells intact even after a normal Ctrl-L redraw.
if !empty($IPDB_TMUX_SOCKET)
  set nolazyredraw
  if exists('&ttyscroll')
    set ttyscroll=0
  endif
  if exists('&t_ut')
    let &t_ut = ''
  endif
endif
let s:last = ''
highlight default IpdbExecution ctermbg=DarkGrey guibg=#444444
sign define IpdbExecution text=>> texthl=Search linehl=IpdbExecution

function! IpdbExecutionCursor() abort
  let overlap = bufnr('%') == get(g:, 'ipdb_execution_buffer', -1)
        \ && line('.') == get(g:, 'ipdb_execution_line', -1)
  if overlap && &l:cursorline
    let w:ipdb_restore_cursorline = 1
    setlocal nocursorline
  elseif !overlap && get(w:, 'ipdb_restore_cursorline', 0)
    setlocal cursorline
    let w:ipdb_restore_cursorline = 0
  endif
endfunction

augroup IpdbExecutionCursor
  autocmd!
  autocmd CursorMoved,CursorMovedI,BufEnter,WinEnter * call IpdbExecutionCursor()
  autocmd ColorScheme * highlight default IpdbExecution ctermbg=DarkGrey guibg=#444444
augroup END
" Defer full redraw until window events finish; never redraw recursively.
let s:screen_layout = ''
let s:redraw_timer = -1
let s:redrawing = 0
let s:force_redraw = 0
let s:client_refresh_timer = -1
let s:client_refresh_job = v:null
function! IpdbRefreshClient(timer) abort
  let s:client_refresh_timer = -1
  if exists('*job_status') && type(s:client_refresh_job) == v:t_job && job_status(s:client_refresh_job) ==# 'run'
    let s:client_refresh_timer = timer_start(30, function('IpdbRefreshClient'))
    return
  endif
  " Never repaint the client over a live debugger/readline prompt.
  let command = [$IPDB_TMUX_BIN, '-L', $IPDB_TMUX_SOCKET, 'if-shell', '-F',
        \ '#{==:#{pane_id},' . $TMUX_PANE . '}', 'refresh-client']
  if exists('*job_start')
    let s:client_refresh_job = job_start(command, {'out_io': 'null', 'err_io': 'null'})
  else
    call system(join(map(copy(command), 'shellescape(v:val)'), ' ') . ' 2>/dev/null')
  endif
endfunction
function! IpdbQueueClientRefresh() abort
  if empty($IPDB_TMUX_SOCKET) || empty($IPDB_TMUX_BIN) || $IPDB_VIM_CLIENT_REFRESH ==# '0'
    return
  endif
  if s:client_refresh_timer != -1
    call timer_stop(s:client_refresh_timer)
  endif
  " Run after Vim flushes its drawing commands; coalesce rapid navigation.
  let s:client_refresh_timer = timer_start(30, function('IpdbRefreshClient'))
endfunction
function! IpdbViewSignature() abort
  let view = winsaveview()
  return string([win_getid(), bufnr('%'), b:changedtick, view,
        \ winwidth(0), winheight(0), &wrap, &foldenable, &foldlevel,
        \ foldclosed(line('.')), foldclosedend(line('.')), @/, &hlsearch, v:hlsearch])
endfunction
function! IpdbSourceRedraw(force) abort
  if mode(1) !~# '^n' && !(mode(1) =~# '^c' && index(['/', '?'], getcmdtype()) >= 0)
    return 0
  endif
  if s:redrawing
    return 0
  endif
  let layout = IpdbViewSignature()
  if a:force || layout !=# s:screen_layout
    let s:redrawing = 1
    try
      if has('gui_running') || (has('ttyin') && has('ttyout'))
        redraw!
        call IpdbQueueClientRefresh()
      endif
      let s:screen_layout = IpdbViewSignature()
    finally
      let s:redrawing = 0
    endtry
    return 1
  endif
  return 0
endfunction
function! IpdbDeferredRedraw(timer) abort
  let s:redraw_timer = -1
  let force = s:force_redraw
  let s:force_redraw = 0
  call IpdbSourceRedraw(force)
endfunction
function! IpdbQueueRedraw(...) abort
  if a:0 && a:1
    let s:force_redraw = 1
  endif
  if s:redraw_timer == -1 && !s:redrawing
    let s:redraw_timer = timer_start(10, function('IpdbDeferredRedraw'))
  endif
endfunction
function! IpdbSearchRedraw() abort
  if index(['/', '?'], getcmdtype()) >= 0
    call IpdbQueueRedraw(1)
  endif
endfunction
augroup PortableIpdbRedraw
  autocmd!
  autocmd CursorMoved,CursorMovedI * call IpdbQueueRedraw()
  autocmd WinEnter,BufWinEnter,VimResized,TabEnter,FocusGained,InsertLeave * call IpdbQueueRedraw(1)
  autocmd TextChanged,TextChangedI * call IpdbQueueRedraw(1)
  if exists('##CmdlineChanged')
    autocmd CmdlineChanged * call IpdbSearchRedraw()
  endif
  if exists('##CmdlineLeave')
    autocmd CmdlineLeave * call IpdbSearchRedraw()
  endif
  if exists('##WinScrolled')
    autocmd WinScrolled * call IpdbQueueRedraw()
  endif
augroup END
let s:probe_token = ''
function! IpdbDiagnosticPoll() abort
  let request = $IPDB_VIM_STATE . '.probe-request'
  if !filereadable(request)
    return
  endif
  try
    let message = json_decode(join(readfile(request), "\n"))
    if message.token ==# s:probe_token
      return
    endif
    let cells = []
    for row in range(1, &lines)
      let text = ''
      for column in range(1, &columns)
        let character = screenchar(row, column)
        let text .= character > 0 ? nr2char(character) : ' '
      endfor
      call add(cells, text)
    endfor
    let report = {'token': message.token, 'columns': &columns, 'lines': &lines,
          \ 'mode': mode(1), 'screen': cells, 'view': winsaveview()}
    let response = $IPDB_VIM_STATE . '.probe-response'
    call writefile([json_encode(report)], response . '.tmp')
    call rename(response . '.tmp', response)
    let s:probe_token = message.token
  catch
    " Diagnostic failures must never interrupt the running debugger.
  endtry
endfunction
function! IpdbSourcePoll(timer) abort
  if !empty($IPDB_VIM_STATE)
    try
      let files = sort(map(filter(getbufinfo(), 'v:val.loaded && fnamemodify(v:val.name, ":e") ==# "py"'), 'fnamemodify(v:val.name, ":p")'))
      let encoded = json_encode(files)
      if encoded !=# get(s:, 'buffer_files', '')
        call writefile([encoded], $IPDB_VIM_STATE . '.buffers.tmp')
        call rename($IPDB_VIM_STATE . '.buffers.tmp', $IPDB_VIM_STATE . '.buffers')
        let s:buffer_files = encoded
      endif
    catch
      " Buffer completion must not interrupt source navigation.
    endtry
  endif
  call IpdbDiagnosticPoll()
  " Do not erase Ex output, hit-enter prompts, completion menus or selections.
  " Leave source updates pending until Vim is back in Normal mode.
  if a:timer != 0 && mode(1) !~# '^n'
    return
  endif
  if !empty($IPDB_VIM_CLOSE) && filereadable($IPDB_VIM_CLOSE)
    call timer_stop(g:ipdb_source_timer)
    if !empty(filter(getbufinfo(), 'v:val.changed'))
      call writefile(['kept'], $IPDB_VIM_CLOSED)
      echom 'ipdb finished; unsaved buffers kept open. Save or close Vim manually.'
      return
    endif
    call writefile(['closing'], $IPDB_VIM_CLOSED)
    qa
    return
  endif
  if !filereadable($IPDB_VIM_STATE)
    return
  endif
  let raw = join(readfile($IPDB_VIM_STATE), "\n")
  if raw ==# s:last
    return
  endif
  let previous = win_getid()
  try
    let state = json_decode(raw)
    if !filereadable(state.file)
      let s:last = raw
      return
    endif
    " Window 1 of tab 1 is the top-left source window; other views stay put.
    if !win_gotoid(win_getid(1, 1))
      return
    endif
    if expand('%:p') !=# fnamemodify(state.file, ':p')
      execute 'keepalt keepjumps hide edit ' . fnameescape(state.file)
    elseif !&modified
      let saved_autoread = &l:autoread
      try
        setlocal autoread
        checktime
      finally
        let &l:autoread = saved_autoread
      endtry
    endif
    " Timer-driven first loads may miss filetype/syntax autocommands.
    " Repair only missing buffer state; preserve user syntax and colors.
    if empty(&l:filetype)
      filetype detect
      if empty(&l:filetype) && state.file =~# '\.pyw\?$'
        setfiletype python
      endif
    endif
    if has('syntax') && !empty(&l:filetype) && !exists('b:current_syntax')
      let &l:syntax = empty(&l:syntax) ? &l:filetype : &l:syntax
    endif
    " An initialized option/marker does not prove syntax rules were loaded.
    " Load them explicitly once for each source buffer, including timer opens.
    if has('syntax') && !get(b:, 'ipdb_syntax_ready', 0)
      let syntax_name = empty(&l:syntax) ? &l:filetype : &l:syntax
      if syntax_name =~# '^\w\+\(\.\w\+\)*$'
        syntax clear
        unlet! b:current_syntax
        for syntax_part in split(syntax_name, '\.')
          execute 'runtime! syntax/' . syntax_part . '.vim'
        endfor
        let b:ipdb_syntax_ready = exists('b:current_syntax')
      endif
    endif
    call cursor(state.line, 1)
    normal! zvzz
    silent! sign unplace 7351
    execute 'sign place 7351 line=' . state.line . ' name=IpdbExecution buffer=' . bufnr('%')
    let g:ipdb_execution_buffer = bufnr('%')
    let g:ipdb_execution_line = state.line
    for source_window in getwininfo()
      if source_window.bufnr == g:ipdb_execution_buffer || getwinvar(source_window.winid, 'ipdb_restore_cursorline', 0)
        if win_gotoid(source_window.winid)
          call IpdbExecutionCursor()
        endif
      endif
    endfor
    let s:last = raw
    let g:ipdb_source_error = ''
  catch
    let g:ipdb_source_error = v:exception
    let s:last = raw
    echohl WarningMsg
    echom 'ipdb source sync: ' . v:exception
    echohl None
  finally
    call win_gotoid(previous)
    call IpdbSourceRedraw(1)
    call IpdbQueueRedraw(1)
  endtry
endfunction
let g:ipdb_source_timer = timer_start(100, function('IpdbSourcePoll'), {'repeat': -1})
call writefile(['ready'], $IPDB_VIM_READY)

" Keep window movement inside Vim until it reaches a terminal pane boundary.
let s:conemu_keys = !empty($IPDB_CONEMU_HELPER) && $ConEmuServerPID =~# '^[1-9][0-9]*$'
function! s:RunConEmuCommand(path_names, arguments) abort
  if exists('*job_start')
    let command = map(copy(a:path_names), {_, name -> eval('$' . name)})
    call job_start(command + a:arguments, {'in_io': 'null', 'out_io': 'null', 'err_io': 'null'})
    return
  endif
  let shell = fnamemodify(substitute(&shell, '^"\|"$', '', 'g'), ':t:r')
  if shell ==? 'powershell' || shell ==? 'pwsh'
    let command = ['&'] + map(copy(a:path_names), {_, name -> '$env:' . name})
  elseif shell ==? 'cmd'
    " Delay expansion until cmd has parsed redirects and command separators.
    let command = ['cmd', '/d', '/v:on', '/c', 'if', 'defined', a:path_names[0]]
          \ + map(copy(a:path_names), {_, name -> '"!' . name . '!"'})
  else
    " Preserve the quotes passed through Vim's Windows shell command line.
    let quote = has('win32') && &shellxquote ==# '"' ? '\"' : '"'
    let command = map(copy(a:path_names), {_, name -> quote . '$' . name . quote})
  endif
  call system(join(command + a:arguments, ' '))
endfunction

function! IpdbWindowMove(direction) abort
  let previous = win_getid()
  execute 'wincmd ' . a:direction
  if win_getid() != previous
    return
  endif
  if !empty($IPDB_TMUX_SOCKET)
    let direction = {'h': '-L', 'j': '-D', 'k': '-U', 'l': '-R'}[a:direction]
    let tmux = shellescape($IPDB_TMUX_BIN) . ' -L ' . shellescape($IPDB_TMUX_SOCKET)
    let target = trim(system(tmux . ' select-pane ' . direction . ' \; display-message -p "#{pane_id}"'))
    if v:shell_error == 0 && target =~# '^%\d\+$' && target !=# $TMUX_PANE
      if s:redraw_timer != -1
        call timer_stop(s:redraw_timer)
        let s:redraw_timer = -1
      endif
      if s:client_refresh_timer != -1
        call timer_stop(s:client_refresh_timer)
        let s:client_refresh_timer = -1
      endif
      let s:force_redraw = 0
      " Preserve command text already delivered to Vim during the pane switch.
      let keys = []
      while 1
        let pending = getchar(1)
        if type(pending) != type(0) || pending == 0 || pending == 23
          break
        endif
        let character = getchar(0)
        if character == 10 || character == 13
          call add(keys, 'Enter')
        elseif character == 8 || character == 127
          call add(keys, 'BSpace')
        elseif character == 27
          call add(keys, 'Escape')
        elseif character < 32
          call add(keys, 'C-' . nr2char(character + 96))
        else
          call add(keys, shellescape(nr2char(character)))
        endif
      endwhile
      if !empty(keys)
        call system(tmux . ' send-keys -t ' . shellescape(target) . ' ' . join(keys, ' '))
      endif
      return
    endif
    call IpdbSourceRedraw(1)
  elseif s:conemu_keys
    let offset = {'h': [-1, 0], 'j': [0, 1], 'k': [0, -1], 'l': [1, 0]}[a:direction]
    call s:RunConEmuCommand(['IPDB_CONEMU_HELPER'], ['-GuiMacro:' . $ConEmuServerPID, 'Split', '2'] + offset)
  endif
endfunction
if !empty($IPDB_TMUX_SOCKET) || s:conemu_keys
  nnoremap <silent> <C-w>h :call IpdbWindowMove('h')<CR>
  nnoremap <silent> <C-w>j :call IpdbWindowMove('j')<CR>
  nnoremap <silent> <C-w>k :call IpdbWindowMove('k')<CR>
  nnoremap <silent> <C-w>l :call IpdbWindowMove('l')<CR>
  nnoremap <silent> <C-w><C-h> :call IpdbWindowMove('h')<CR>
  nnoremap <silent> <C-w><C-j> :call IpdbWindowMove('j')<CR>
  nnoremap <silent> <C-w><C-k> :call IpdbWindowMove('k')<CR>
  nnoremap <silent> <C-w><C-l> :call IpdbWindowMove('l')<CR>
endif

function! s:ConEmuResize(action, count) abort
  if winnr('$') > 1
    execute a:count . 'wincmd ' . a:action
    return
  endif
  call s:RunConEmuCommand(['IPDB_CONEMU_PYTHON', 'IPDB_CONEMU_LAUNCHER'],
        \ ['--resize', a:action, string(a:count)])
endfunction
if s:conemu_keys && empty($IPDB_TMUX_SOCKET) && !empty($IPDB_CONEMU_CONFIG)
      \ && !empty($IPDB_CONEMU_PYTHON) && !empty($IPDB_CONEMU_LAUNCHER)
  nnoremap <silent> <C-w>+ :<C-u>call <SID>ConEmuResize('+', v:count1)<CR>
  nnoremap <silent> <C-w>- :<C-u>call <SID>ConEmuResize('-', v:count1)<CR>
  nnoremap <silent> <C-w>= :<C-u>call <SID>ConEmuResize('=', 1)<CR>
endif

command! IpdbViewInfo echomsg string({'term': $TERM, 'vim': v:version, 'size': [&lines, &columns], 'view': winsaveview(), 'lazyredraw': &lazyredraw, 'ttyscroll': &ttyscroll, 'background_erase': &t_ut})

" Explicit diagnostics only: no polling overhead and no automatic uploads.
function! IpdbViewDump(path) abort
  let output = empty(a:path) ? tempname() . '.json' : expand(a:path)
  let options = {}
  for name in ['term', 'encoding', 'termencoding', 'ambiwidth', 'display',
        \ 'lines', 'columns', 'wrap', 'linebreak', 'breakindent', 'number',
        \ 'relativenumber', 'list', 'listchars', 'scrolloff', 'sidescrolloff',
        \ 'lazyredraw', 'ttyfast', 'ttyscroll', 'termguicolors', 'synmaxcol',
        \ 't_Co', 't_ce', 't_cd', 't_cl', 't_cs', 't_cm', 't_al', 't_dl',
        \ 't_AL', 't_DL', 't_ut', 't_ti', 't_te']
    if exists('&' . name)
      let options[name] = eval('&' . name)
    endif
  endfor
  let cells = []
  for row in range(1, &lines)
    let text = ''
    for column in range(1, &columns)
      let character = screenchar(row, column)
      let text .= character > 0 ? nr2char(character) : ' '
    endfor
    call add(cells, text)
  endfor
  let report = {'version': execute('version'), 'term': $TERM,
        \ 'options': options, 'view': winsaveview(), 'file': expand('%:p'),
        \ 'visible_source': getline(line('w0'), line('w$')),
        \ 'vim_screen': cells, 'scripts': execute('scriptnames'),
        \ 'messages': execute('messages'), 'source_error': get(g:, 'ipdb_source_error', ''),
        \ 'tmux_pane': $TMUX_PANE, 'konsole_version': $KONSOLE_VERSION}
  if !empty($IPDB_TMUX_SOCKET) && !empty($IPDB_TMUX_BIN) && !empty($TMUX_PANE)
    let command = shellescape($IPDB_TMUX_BIN) . ' -L ' . shellescape($IPDB_TMUX_SOCKET)
    let report.tmux_screen = systemlist(command . ' capture-pane -p -t ' . shellescape($TMUX_PANE))
    let report.tmux_version = systemlist(shellescape($IPDB_TMUX_BIN) . ' -V')
    let report.tmux_panes = systemlist(command . ' list-panes -F ' . shellescape('#{pane_id} #{pane_width}x#{pane_height} #{pane_current_command}'))
  endif
  call writefile([json_encode(report)], output)
  echom 'ipdb view diagnostic saved: ' . fnamemodify(output, ':p')
endfunction
command! -nargs=? -complete=file IpdbViewDump call IpdbViewDump(<q-args>)
