" System Vim bridge. Loaded after the user's vimrc and plugins.
if !exists('*timer_start') || !exists('*json_decode') || !exists('*win_getid')
  call writefile(['System Vim requires timers, JSON and window IDs'], $IPDB_VIM_READY)
  finish
endif
" Route clicks to the corresponding Vim window instead of terminal selection.
set mouse=a
let s:last = ''
highlight default IpdbExecution ctermbg=DarkGrey guibg=#444444
sign define IpdbExecution text=>> texthl=Search linehl=IpdbExecution
" Defer full redraw until window events finish; never redraw recursively.
let s:screen_layout = ''
let s:redraw_timer = -1
let s:redrawing = 0
let s:force_redraw = 0
function! IpdbSourceRedraw(force) abort
  if s:redrawing
    return 0
  endif
  let layout = string([win_getid(), winsaveview().topline, winsaveview().leftcol, winwidth(0), winheight(0)])
  if a:force || layout !=# s:screen_layout
    let s:redrawing = 1
    try
      if has('gui_running') || (has('ttyin') && has('ttyout'))
        redraw!
      endif
      let s:screen_layout = string([win_getid(), winsaveview().topline, winsaveview().leftcol, winwidth(0), winheight(0)])
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
augroup PortableIpdbRedraw
  autocmd!
  autocmd CursorMoved,WinEnter,BufWinEnter,VimResized * call IpdbQueueRedraw()
  if exists('##WinScrolled')
    autocmd WinScrolled * call IpdbQueueRedraw()
  endif
augroup END
function! IpdbSourcePoll(timer) abort
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
    endif
    call cursor(state.line, 1)
    normal! zvzz
    silent! sign unplace 7351
    execute 'sign place 7351 line=' . state.line . ' name=IpdbExecution buffer=' . bufnr('%')
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

" Keep native Vim movement inside Vim; cross the tmux boundary at an edge.
function! IpdbWindowMove(direction) abort
  let previous = win_getid()
  execute 'wincmd ' . a:direction
  if win_getid() == previous && !empty($IPDB_TMUX_SOCKET)
    let direction = {'h': '-L', 'j': '-D', 'k': '-U', 'l': '-R'}[a:direction]
    call system(shellescape($IPDB_TMUX_BIN) . ' -L ' . shellescape($IPDB_TMUX_SOCKET) . ' select-pane ' . direction)
    call IpdbSourceRedraw(1)
  endif
endfunction
if !empty($IPDB_TMUX_SOCKET)
  nnoremap <silent> <C-w>h :call IpdbWindowMove('h')<CR>
  nnoremap <silent> <C-w>j :call IpdbWindowMove('j')<CR>
  nnoremap <silent> <C-w>k :call IpdbWindowMove('k')<CR>
  nnoremap <silent> <C-w>l :call IpdbWindowMove('l')<CR>
endif
