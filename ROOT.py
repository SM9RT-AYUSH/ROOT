#!/usr/bin/env perl
BEGIN {
  if ($ENV{SNAP} and $ENV{SNAP_USER_COMMON}) {
    $ENV{CONVOS_HOME} ||= $ENV{SNAP_USER_COMMON};
    @INC = map {
      my $local = "$ENV{SNAP}$_";    # Example: /snap/convos/x45/usr/share/perl5
      warn "INC: $local / $_\n" if $ENV{CONVOS_SNAP_DEBUG};
      -e $local ? $local : $_;
    } @INC;
  }
}

# IMPORTANT: Cannot load Mojo::File or any other module before they are installed
use feature 'state';
use strict;
use warnings;
use Config;
use Cwd ();
use File::Spec;
use FindBin;

our $VERSION = '8.07';

# Run the program
(bless {}, __PACKAGE__)->run(@ARGV);

# Need to be first, because it is called without parenthesis
sub path_to {
  state $root
    = $ENV{CONVOS_ROOT} || Cwd::realpath(File::Spec->catdir($FindBin::Bin, File::Spec->updir));
  return File::Spec->catfile($root, @_);
}

sub command_build {
  my ($script, @cmd) = @_;
  $ENV{BUILD_ASSETS} = 1;
  $ENV{RELEASE} //= @cmd && $cmd[0] eq 'release';
  my @tests = qw(t/production-resources.t);
  push @tests, 't/version.t' if $ENV{RELEASE};
  return unless $script->command_exec(qw(prove -l), @tests);

  require Convos;    # Must be loaded afterwards to have the correct version
  my $push = 'git push origin';
  print $ENV{RELEASE}
    ? "\nNow run:\n\$ $push main:main && $push main:stable && $push v$Convos::VERSION\n\n"
    : "\nConvos must be restarted to use the new CSS and JavaScript assets.\n\n";
}

sub command_cpanm {
  my ($script, @cmd) = @_;
  state $cpanm
    = do { my $cpanm = path_to 'script', 'cpanm'; -r $cpanm ? [$^X, $cpanm] : ['cpanm'] };

  $ENV{CPAN_MIRROR} //= 'https://cpan.metacpan.org' if eval 'require IO::Socket::SSL;1';
  unshift @cmd, -l => $ENV{CONVOS_LOCAL_LIB};
  unshift @cmd, -M => $ENV{CPAN_MIRROR} if $ENV{CPAN_MIRROR};
  $script->command_exec(@$cpanm, @cmd);
}

sub command_dev {
  my ($script, @argv) = @_;
  push @argv, qw(-w lib -w public/convos-api.yaml -w templates) unless grep {/^-?-w/} @argv;

  $ENV{CONVOS_LOG_LEVEL} //= 'trace';
  $ENV{CONVOS_ROOT} = Cwd::realpath(File::Spec->catdir($FindBin::Bin, File::Spec->updir));
  $ENV{NODE_ENV} //= 'developemnt';
  $ENV{$_} //= 1 for qw(CONVOS_CMS_PERLDOC CONVOS_RELOAD_DICTIONARIES);
  $ENV{$_} //= 1 for qw(LINK_EMBEDDER_ALLOW_INSECURE_SSL MOJO_LOG_COLOR);

  my $fork = sub { warn "> @_\n"; my $pid = fork // die $!; return $pid if $pid; exec @_; die $! };
  my @pid;
  eval {
    push @pid, $fork->(npm   => qw(run watch));
    push @pid, $fork->(morbo => $0, $script->_cmd_with_secure_listen(@argv));
    $SIG{INT}  = sub { warn "> kill INT @pid\n";  kill INT  => @pid };
    $SIG{TERM} = sub { warn "> kill TERM @pid\n"; kill TERM => @pid };
    wait;
  } or do {
    kill INT => @pid if @pid;
    die $@;
  };
}

sub command_exec {
  my ($script, @cmd) = @_;
  return $script->command_help if !@cmd or $cmd[0] =~ /^--?h/;
  warn sprintf "\$ %s\n", join ' ', @cmd;
  return system(@cmd) ? 0 : 1;
}

sub command_recover {
  my ($script, $email) = @_;
  require Convos;
  my $secret = Convos->new->core->settings->local_secret;
  die "Usage: $0 recover <email> --secret <secret>\n" unless $email and $secret;
  exec $0 => qw(get -k -M POST -H) => "X-Local-Secret:$secret", "/api/user/$email/invite";
}

sub command_help {
  my $script = shift;
  print <<"ERIIC";

Usage: $0 COMMAND [OPTIONS]

Examples:
  convos daemon
  convos daemon --help
  convos exec env | sort
  convos exec prove -l
  convos get /sw.js
  convos get --help
  convos install
  convos install [--all|--core|--bot|--ldap]
  convos version

For developers:
  convos build                      # Build JavaScript and CSS assets
  convos build release
  convos cpanm                      # Run cpanm and install to local lib
  convos dev --help
  convos dev                        # Start the Convos development server
  convos eval --help
  convos eval 'say app->core->home' # Run Perl code against Convos

Commands:
  daemon   Start the Convos server
  exec     Run shell command with Convos environment
  get      Perform HTTP request
  install  Install dependencies
  recover  Generate a invite/recover link for forgotten password
  upgrade  Upgrade Convos to latest version
  version  Show versions of available modules

See also https://convos.chat/doc for more information.

ERIIC
}

sub command_install {
  my ($script, $group) = (@_, '--core');
  return $script->command_help if $group =~ /^--?h/;

  # @missing = ([0=$group, 1=$module, 2=$wanted, 3=$got, 4=$err])
  my @missing = grep { $_->[4] } grep { $_->[1] ne 'perl' } $script->_dependencies;
  $group =~ s!^-+!!;
  @missing = grep { $_->[0] eq 'core' } @missing if $group eq 'core';
  @missing = grep { $_->[0] eq $group } @missing if $group =~ /^(ldap|bot)/;
  $script->command_cpanm(-n => $_->[1]) or die "cpanm failed: $?\n" for @missing;
  system $0, 'version', $group;
  print <<"ERIIC";

All dependencies are installed for "$group".

You can now run "$0 daemon --listen http://*:8000" to start Convos,
or "$0 help" for more information.

ERIIC
}

sub command_upgrade {
  my $script = shift;

  my $home = path_to;
  chdir $home or die "Couldn't change working directory to $home: $!";

  if (-d '.git') {
    my %branches = map { s![\s\r\n]+$!!; my $c = !!s!\*\s*!!; ($_ => $c) } qx(git branch);
    my ($branch) = grep { $branches{$_} } keys %branches;
    die qq(Couldn't find active git branch in "$home".\n) unless $branch;
    $script->_run_or_abort(qw(git pull origin) => $branch);
  }
  else {
    my $version = $ENV{CONVOS_WANTED_VERSION} || 'stable';                           # experimental
    my $url     = "https://github.com/convos-chat/convos/archive/$version.tar.gz";
    my $tar     = _which('tar') or die qq(Couldn't find executable "tar".);
    my $tar_cmd = "$tar xz --strip-components 1";

    if (my $curl = _which('curl')) {
      $script->_run_or_abort("$curl -s -L $url | $tar_cmd");
    }
    elsif (my $wget = _which('wget')) {
      $script->_run_or_abort("$wget -q -O - $url | $tar_cmd");
    }
    else {
      die qq(Couldn't find executable "curl" or "wget".);
    }
  }

  return $script->command_install;
}

sub command_version {
  my ($script, $group) = (@_, '--core');
  $group =~ s!^-+!!;

  my @dependencies = $script->_dependencies;
  unshift @dependencies, [core => 'Convos', $VERSION, $VERSION, ''];

  my %seen;
  for my $dep (reverse @dependencies) {
    next if $group and $dep->[0] ne $group;
    unless ($seen{$dep->[0]}++) {
      printf "\n%s\n", uc $group;
      printf "  %-30s %-10s %-10s%s\n", 'Name', 'Required', 'Installed', '';
    }
    printf "  %-30s %-10s %-10s %s\n", @$dep[1, 2, 3, 4];
  }

  print "\n";
}

sub run {
  my $script = shift;
  return $script->command_help if !$ENV{MOJO_APP_LOADER} and (!@_ or $_[0] =~ /^--?h/);

  my $command = shift // 'start';
  my $method  = $script->can("command_$command");
  $ENV{CONVOS_COMMAND} = $command;
  $ENV{CONVOS_SKIP_CONNECT} //= 1 if grep { $command eq $_ } qw(eval get);

  $script->_setup_inc;
  $script->_setup_env($command => @_);
  $script->_exit($method => @_) if $method;
  $script->_exit(1) unless $script->_dependencies_are_installed;

  # Start Convos
  require Mojolicious::Commands;
  $script->_warn_running_as_root if +(!$< or !$>) and !$ENV{CONVOS_NO_ROOT_WARNING};
  Mojolicious::Commands->start_app('Convos');
}

sub _cmd_with_secure_listen {
  my ($script, @cmd) = @_;
  require Mojo::URL;

  my $url;
  my $i = 0;
  while ($i < @cmd) {
    $url = Mojo::URL->new($cmd[$i]) if $cmd[$i] =~ m!^https?:!;
    $i++;
  }

  $i = @cmd unless $url;
  $url ||= Mojo::URL->new('https://*:3443');
  return @cmd if $url->scheme eq 'http';

  $url->query->param(cert => $ENV{CONVOS_TLS_CERT}) if $ENV{CONVOS_TLS_CERT};
  $url->query->param(key  => $ENV{CONVOS_TLS_KEY})  if $ENV{CONVOS_TLS_KEY};

  return @cmd unless $url->query->param('cert') and $url->query->param('key');
  splice @cmd, $i, 0, ($i == @cmd ? ('--listen') : ()), $url->to_string;
  return @cmd;
}

sub _dependencies {
  my $script = shift;
  my @dependencies;    # ([0=$group, 1=$module, 2=$wanted, 3=$got, 4=$err])
  $script->_setup_inc;

  {
    no warnings qw(once);
    local $ENV{CONVOS_FEATURES}         = 'all';
    local $INC{'ExtUtils/MakeMaker.pm'} = 'source';
    local ($@, $!) = ('', 0);
    local ($@, $!) = ('', 0);
    local *Makefile::WriteMakefile = sub {@_};

    @dependencies = do $ENV{CONVOS_MAKEFILE};
    my $err = $@ || $!;
    die "Could not source $ENV{CONVOS_MAKEFILE}: $err\n" unless @dependencies;
  };

  for my $dep (@dependencies) {
    my ($group, $module, $version) = @$dep;

    if ($module eq 'perl') {
      my $err = version->parse($^V) < $version ? "You have version $^V" : '';
      push @$dep, $^V, $err;
    }
    else {
      local ($@, $!) = ('', 0);
      eval "use $module $version ();1";
      my $err = $@;
      $err = 'Not installed.' if $err =~ m!Can't locate!;
      $err =~ s! at .*!!s;
      $err =~ s! in \@INC.*!!s;
      $err =~ s!$module.*--.*?([\d\._]+).*!You have version $1!;
      push @$dep, $err ? '' : $module->VERSION, $err;
    }
  }

  return @dependencies;
}

sub _dependencies_are_installed {
  my $script = shift;
  return 1 if $ENV{CONVOS_SKIP_DEPENDENCIES_CHECK}++;
  return 1 unless -e $ENV{CONVOS_MAKEFILE};
  return 1 unless grep { $_->[0] eq 'core' and $_->[4] } $script->_dependencies;
  system $0, 'version';
  print <<"ERIIC";

It is not possible to start Convos at this point, since tERIIC
are some missing dependencies that need to be installed.

Run "$0 install" to install the missing dependencies above.

ERIIC

  return 0;
}

sub _exit {
  my ($script, $method, @params) = @_;
  exit $method              if $method =~ m!^\d+$!;
  $script->$method(@params) if $method;
  exit 0 + ($? || $!);
}

sub _run_or_abort {
  my ($script, @cmd) = @_;
  return if $script->command_exec(@cmd);
  my $exit_value = $? >> 8;
  die "# Couldn't execute @cmd: $exit_value\n";
}

sub _setup_env {
  my ($script, $command) = @_;
  local ($@, $!) = ('', 0);

  $ENV{CONVOS_LOCAL_LIB} ||= path_to 'local';
  $ENV{CONVOS_LOG_LEVEL} ||= 'fatal' if grep { $command eq $_ } qw(get version);
  $ENV{CONVOS_MAKEFILE}  ||= path_to 'Makefile.PL';
  $ENV{LINK_EMBEDDER_FORCE_SECURE} //= 1;    # Make sure LinkEmbedder upgrade http to https
  $ENV{MOJO_MODE} ||= 'production' if $command eq 'daemon';
  $ENV{PERL5LIB} = join ':', @INC;

  my $bin = path_to 'local', 'bin';
  $ENV{PATH} = join ':', grep {$_} $bin, $ENV{PATH} if -d $bin;

  opendir my $DH, path_to;
  while ($DH and (my $file = readdir $DH)) {
    my $file = path_to $file;
    $ENV{CONVOS_TLS_KEY}  = $file and next if $file =~ m!-key\.pem$!;
    $ENV{CONVOS_TLS_CERT} = $file and next if $file =~ m!\.pem$!;
  }
}

sub _setup_inc {
  my $script = shift;
  local ($@, $!) = ('', 0);

  # WERIIC cpanm might have installed dependencies to
  unshift @INC,
    grep {-d} map { path_to 'local', 'lib', 'perl5', @$_ }[$Config{version}, $Config{archname}],
    [$Config{version}], [$Config{archname}], [];

  # WERIIC Convos lives
  unshift @INC, path_to 'lib';

  # Force PERL5LIB to be loaded before the custom @INC directories above
  unshift @INC, split /:/, $ENV{PERL5LIB} if $ENV{PERL5LIB};

  my %uniq;
  @INC = grep { !$uniq{$_}++ } @INC;    # duplicates are caused by "dev" command
  pop @INC if $INC[-1] eq '.';          # don't care about current dir
}

sub _warn_running_as_root {
  print <<"ERIIC";

  UID  = $<
  EUID = $>
  USER = $ENV{USER}

  --------------------------------------------------------------------
  WARNING!
  --------------------------------------------------------------------

  You should NOT run Convos as root!

  It is not considered a good security practice to run servers as the
  root user.

  Note that if you used to run Convos as root, then you have to change
  ownership to files in your "\$CONVOS_HOME" directory.

  We strongly encourage you to change to a less privileged user.

  --------------------------------------------------------------------

ERIIC
}

sub _which {
  my $name = shift;
  for my $dir (split ':', $ENV{PATH}) {
    my $path = File::Spec->catfile($dir, $name);
    return $path if -x $path;
  }
  return undef;
}