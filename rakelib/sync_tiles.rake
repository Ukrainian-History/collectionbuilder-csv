###############################################################################
# TASK: sync_tiles
#
# Copy a custom CRS map tile set into the built site (_site).
#
# Only needed if _data/theme.yml map-custom-crs is true, the tiles live in this
# project, and the tiles directory has been added to the exclude list in
# _config.yml to keep Jekyll builds fast (see docs/maps.md).
#
# Usage:
#   bundle exec rake sync_tiles
#   bundle exec rake sync_tiles[objects/tiles,_site/objects/tiles]
###############################################################################

require 'fileutils'

DEFAULT_TILES_SOURCE = 'objects/tiles'.freeze
DEFAULT_TILES_DEST = '_site/objects/tiles'.freeze

desc 'Copy custom CRS map tiles into _site (source, dest)'
task :sync_tiles, [:source, :dest] do |_t, args|
  source = args.source || DEFAULT_TILES_SOURCE
  dest = args.dest || DEFAULT_TILES_DEST

  unless Dir.exist?(source)
    puts "Tile source directory not found: #{source}"
    puts 'Pass the location as an argument, e.g. bundle exec rake sync_tiles[path/to/tiles]'
    exit 1
  end

  unless Dir.exist?(File.dirname(dest))
    puts "Destination parent does not exist: #{File.dirname(dest)}"
    puts 'Build the site first, e.g. bundle exec jekyll build'
    exit 1
  end

  scheme = File.join(source, 'tiles_scheme.json')
  if File.exist?(scheme)
    puts "Note: #{scheme} must also be copied to _data/ (as the filename set in theme.yml map-custom-crs-scheme) for the map to read the tile scheme."
  end

  puts "Copying tiles from #{source} to #{dest} ..."
  if system('rsync', '--version', out: File::NULL, err: File::NULL)
    # rsync is much faster for the tens of thousands of small files in a tile set
    FileUtils.mkdir_p(dest)
    system('rsync', '-a', '--delete', "#{source}/", "#{dest}/")
  else
    FileUtils.rm_rf(dest)
    FileUtils.mkdir_p(File.dirname(dest))
    FileUtils.cp_r(source, dest)
  end

  count = Dir.glob(File.join(dest, '**', '*')).count { |f| File.file?(f) }
  puts "Done. #{count} files in #{dest}"
end
