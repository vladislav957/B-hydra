#include <D2D>


using boo::asio::ip::udp;

void discver_neghbors(){
     boost::asio::io_context io_context;
     udp:;scoket socket(io_contxt,udp::endpoint(udp::v4(),0));
     sockrt.set_option(boost::asio::socket_base::broadcast(true));
     
     udp::endpoint broadcast_endpoint(boost::asio::ip::address_v4::broadcast(),8080);
     
     std::string discovery_message = "...";
     socket.send_to(boost::asio::buffer(discovery_message),broadcast_endpoint);
     ruturn 0;
}